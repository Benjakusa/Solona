"""Authenticated idempotent device event inbox for offline clients."""
import hmac
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.core.security import hash_token
from app.db.session import get_db, set_tenant_context
from app.models import (
    AuditEvent, Customer, Device, Medicine, MedicineBatch, Payment, Permission,
    Purchase, PurchaseItem, RolePermission, Shop, ShopAssignment, StockMovement,
    Supplier, SyncEvent, TenantMembership, User,
)
from app.schemas import SyncPushIn
from app.services import pharmacy as pharmacy_service

router = APIRouter(prefix="/tenants/{tenant_id}/shops/{shop_id}/sync", tags=["offline synchronization"])


def _authorized_device(db: Session, tenant_id: UUID, shop_id: UUID,
                       device_id: UUID, credential: str) -> Device:
    set_tenant_context(db, tenant_id)
    device = db.scalar(select(Device).where(
        Device.tenant_id == tenant_id,
        Device.shop_id == shop_id,
        Device.id == device_id,
    ))
    if device is None or device.revoked_at is not None or not hmac.compare_digest(
        device.credential_hash, hash_token(credential)
    ):
        raise ApiError(401, "device_unauthorized", "The device is not authorized.")
    if db.get(Shop, shop_id) is None:
        raise ApiError(404, "shop_not_found", "Shop not found.")
    return device


@router.post("/events")
def push_events(tenant_id: UUID, shop_id: UUID, payload: SyncPushIn,
                x_device_id: UUID = Header(), x_device_secret: str = Header(min_length=40, max_length=200),
                db: Session = Depends(get_db)):
    device = _authorized_device(db, tenant_id, shop_id, x_device_id, x_device_secret)
    acknowledgments = []
    for event in payload.events:
        if (event.tenant_id, event.shop_id, event.device_id) != (tenant_id, shop_id, device.id):
            raise ApiError(403, "event_scope_mismatch", "An event does not match this registered device scope.")
        existing = db.scalar(select(SyncEvent).where(
            SyncEvent.tenant_id == tenant_id, SyncEvent.event_id == event.event_id,
        ))
        if existing:
            incoming_payload = event.model_dump(mode="json")
            same_content = (
                existing.device_id == device.id
                and existing.transaction_id == event.transaction_id
                and existing.event_type == event.event_type
                and existing.payload == incoming_payload
            )
            if not same_content:
                raise ApiError(409, "event_id_reused", "An event identifier was reused with different content.")
            acknowledgments.append({"event_id": str(event.event_id), "status": existing.status, "duplicate": True})
            continue
        transaction = db.scalar(select(SyncEvent).where(
            SyncEvent.tenant_id == tenant_id,
            SyncEvent.transaction_id == event.transaction_id,
        ))
        if transaction:
            if transaction.device_id == device.id and transaction.payload == event.model_dump(mode="json"):
                acknowledgments.append({"event_id": str(event.event_id), "status": transaction.status, "duplicate": True})
                continue
            raise ApiError(409, "transaction_id_reused", "A transaction identifier was already received.")
        status = "needs_review"
        domain_payload = dict(event.payload.get("payload", event.payload))
        domain_payload["shop_id"] = str(shop_id)
        domain_payload["occurred_at"] = event.occurred_at.isoformat()
        error_code = "event_type_requires_reconciliation"
        if event.event_type in {"sale.created", "sale.returned", "medicine.created", "medicine.updated", "medicine.deactivated", "purchase.received", "stock.adjusted"}:
            try:
                actor_id = UUID(domain_payload["cashier_cloud_id"])
                permission_code = "sales:write" if event.event_type in {"sale.created", "sale.returned"} else "inventory:write"
                membership = db.scalar(select(TenantMembership).where(
                    TenantMembership.tenant_id == tenant_id,
                    TenantMembership.user_id == actor_id,
                    TenantMembership.is_active.is_(True),
                ))
                allowed = None
                if membership:
                    allowed = db.scalar(select(RolePermission.id)
                        .join(Permission, Permission.id == RolePermission.permission_id)
                        .where(RolePermission.tenant_id == tenant_id,
                               RolePermission.role_id == membership.role_id,
                               Permission.code == permission_code))
                assigned = membership and (membership.all_shops or db.scalar(select(ShopAssignment.id).where(
                    ShopAssignment.tenant_id == tenant_id, ShopAssignment.shop_id == shop_id,
                    ShopAssignment.membership_id == membership.id,
                )))
                actor = db.get(User, actor_id)
                if not membership or not allowed or not assigned or not actor or not actor.is_active:
                    raise ApiError(409, "offline_actor_revoked", "The offline staff authorization is no longer active.")
                with db.begin_nested():
                    if event.event_type == "sale.created":
                        pharmacy_service.record_replayed_sale(
                            db, tenant_id, shop_id, actor_id, event.transaction_id, domain_payload,
                        )
                    elif event.event_type == "sale.returned":
                        pharmacy_service.record_replayed_return(
                            db, tenant_id, shop_id, actor_id, event.transaction_id, domain_payload,
                        )
                    elif event.event_type == "medicine.created":
                        _apply_medicine_created(db, tenant_id, actor_id, event.transaction_id, domain_payload)
                    elif event.event_type in {"medicine.updated", "medicine.deactivated"}:
                        _apply_medicine_update(db, tenant_id, actor_id, event.transaction_id, event.event_type, domain_payload)
                    elif event.event_type == "purchase.received":
                        _apply_purchase(db, tenant_id, shop_id, actor_id, event.transaction_id, domain_payload)
                    elif event.event_type == "stock.adjusted":
                        _apply_stock_adjustment(db, tenant_id, shop_id, actor_id, event.transaction_id, domain_payload)
                    status = "applied"
            except ApiError as exc:
                error_code = exc.code
                status = "conflict"
            except (KeyError, ValueError, TypeError):
                error_code = "offline_return_invalid" if event.event_type == "sale.returned" else "offline_sale_invalid"
                status = "conflict"
        db.add(SyncEvent(
            tenant_id=tenant_id, shop_id=shop_id, device_id=device.id,
            event_id=event.event_id, transaction_id=event.transaction_id,
            event_type=event.event_type, schema_version=event.schema_version,
            payload=event.model_dump(mode="json"), status=status,
            created_at=event.occurred_at,
        ))
        acknowledgments.append({"event_id": str(event.event_id), "status": status,
                                "code": error_code if status != "applied" else None,
                                "duplicate": False})
    return {"events": acknowledgments}


def _apply_medicine_created(db: Session, tenant_id: UUID, actor_id: UUID, transaction_id: UUID, data: dict):
    medicine_id = UUID(data["cloud_medicine_id"])
    batch_id = UUID(data["cloud_batch_id"])
    medicine = db.scalar(select(Medicine).where(Medicine.tenant_id == tenant_id, Medicine.id == medicine_id))
    if medicine:
        return medicine
    sku = data.get("sku") or f"local-{medicine_id.hex[:16]}"
    if db.scalar(select(Medicine.id).where(Medicine.tenant_id == tenant_id, Medicine.sku == sku)):
        raise ApiError(409, "offline_sku_conflict", "The medicine identifier conflicts with a cloud catalogue record.")
    medicine = Medicine(id=medicine_id, tenant_id=tenant_id, sku=sku,
                        name=data["name"], generic_name=data.get("generic_name"),
                        manufacturer=data.get("manufacturer"), barcode=data.get("barcode") or None,
                        category=data.get("category"), base_unit="unit",
                        is_controlled=bool(data.get("is_controlled", False)))
    db.add(medicine)
    db.flush()
    batch = MedicineBatch(id=batch_id, tenant_id=tenant_id, shop_id=UUID(data["shop_id"]),
                          medicine_id=medicine.id, batch_no=data["batch_no"],
                          expiry_date=date.fromisoformat(data["expiry_date"]) if data.get("expiry_date") else date.max,
                          quantity=int(data["quantity"]), purchase_cost=data["purchase_cost"],
                          selling_price=data["selling_price"])
    db.add(batch)
    if batch.quantity:
        db.add(StockMovement(tenant_id=tenant_id, shop_id=batch.shop_id, batch_id=batch.id,
                             transaction_id=transaction_id, movement_type="opening", quantity_delta=batch.quantity,
                             actor_id=actor_id))
    return medicine


def _apply_medicine_update(db: Session, tenant_id: UUID, actor_id: UUID, transaction_id: UUID,
                           event_type: str, data: dict):
    medicine_id = UUID(data["cloud_medicine_id"])
    medicine = db.scalar(select(Medicine).where(Medicine.tenant_id == tenant_id, Medicine.id == medicine_id).with_for_update())
    if medicine is None:
        raise ApiError(409, "offline_medicine_unknown", "The medicine is not present in the cloud catalogue.")
    if event_type == "medicine.deactivated":
        medicine.is_active = False
        return medicine
    for field in ("name", "generic_name", "manufacturer", "category", "barcode"):
        if field in data:
            setattr(medicine, field, data[field] or None)
    if "sale_price" in data:
        batches = db.scalars(select(MedicineBatch).where(
            MedicineBatch.tenant_id == tenant_id,
            MedicineBatch.shop_id == UUID(data["shop_id"]),
            MedicineBatch.medicine_id == medicine_id,
            MedicineBatch.quantity > 0,
        ).with_for_update()).all()
        for batch in batches:
            batch.selling_price = data["sale_price"]
    return medicine


def _apply_purchase(db: Session, tenant_id: UUID, shop_id: UUID, actor_id: UUID,
                    transaction_id: UUID, data: dict):
    try:
        supplier_id = UUID(data["cloud_supplier_id"])
        medicine_id = UUID(data["cloud_medicine_id"])
        batch_id = UUID(data["cloud_batch_id"])
    except (KeyError, ValueError):
        raise ApiError(409, "offline_purchase_unmapped", "The supplier or medicine needs cloud reconciliation.") from None
    supplier = db.scalar(select(Supplier).where(Supplier.tenant_id == tenant_id, Supplier.id == supplier_id).with_for_update())
    medicine = db.scalar(select(Medicine).where(Medicine.tenant_id == tenant_id, Medicine.id == medicine_id))
    if supplier is None or medicine is None:
        raise ApiError(409, "offline_purchase_unmapped", "The supplier or medicine needs cloud reconciliation.")
    purchase = Purchase(tenant_id=tenant_id, shop_id=shop_id, supplier_id=supplier_id,
                        transaction_id=transaction_id, reference=data.get("batch_no"))
    db.add(purchase)
    db.flush()
    batch = db.scalar(select(MedicineBatch).where(
        MedicineBatch.tenant_id == tenant_id, MedicineBatch.shop_id == shop_id,
        MedicineBatch.medicine_id == medicine_id, MedicineBatch.id == batch_id,
    ).with_for_update())
    expiry = date.fromisoformat(data["expiry_date"])
    quantity = int(data["quantity"])
    cost = data["purchase_cost"]
    price = data.get("selling_price") or (batch.selling_price if batch else 0)
    if batch:
        if batch.expiry_date != expiry:
            raise ApiError(409, "offline_batch_expiry_conflict", "The batch expiry differs from the cloud record.")
        batch.quantity += quantity
        batch.purchase_cost = cost
        batch.selling_price = price
    else:
        batch = MedicineBatch(id=batch_id, tenant_id=tenant_id, shop_id=shop_id,
                              medicine_id=medicine_id, batch_no=data["batch_no"],
                              expiry_date=expiry, quantity=quantity, purchase_cost=cost,
                              selling_price=price, supplier_id=supplier_id)
        db.add(batch)
        db.flush()
    total = cost * quantity
    paid = data.get("amount_paid", total)
    purchase.total_amount = total
    purchase.amount_paid = paid
    purchase.status = "paid" if paid == total else "partially_paid"
    supplier.payable_balance += total - paid
    db.add(PurchaseItem(tenant_id=tenant_id, purchase_id=purchase.id, shop_id=shop_id,
                        medicine_id=medicine_id, batch_id=batch.id, batch_no=data["batch_no"],
                        expiry_date=expiry, quantity=quantity, purchase_cost=cost, selling_price=price))
    db.add(StockMovement(tenant_id=tenant_id, shop_id=shop_id, batch_id=batch.id,
                         transaction_id=transaction_id, movement_type="offline_purchase",
                         quantity_delta=quantity, actor_id=actor_id))
    if paid > 0:
        db.add(Payment(tenant_id=tenant_id, shop_id=shop_id, purchase_id=purchase.id,
                       transaction_id=transaction_id, amount=paid, currency="KES", method="cash"))
    return purchase


def _apply_stock_adjustment(db: Session, tenant_id: UUID, shop_id: UUID, actor_id: UUID,
                            transaction_id: UUID, data: dict):
    for movement in data.get("movements", []):
        try:
            batch_id = UUID(movement["cloud_batch_id"])
            delta = int(movement.get("quantity_delta", movement.get("delta")))
        except (KeyError, ValueError, TypeError):
            raise ApiError(409, "offline_stock_unmapped", "A stock batch needs cloud reconciliation.") from None
        batch = db.scalar(select(MedicineBatch).where(
            MedicineBatch.tenant_id == tenant_id, MedicineBatch.shop_id == shop_id,
            MedicineBatch.id == batch_id,
        ).with_for_update())
        if batch is None and delta > 0 and data.get("cloud_medicine_id"):
            medicine_id = UUID(data["cloud_medicine_id"])
            batch = MedicineBatch(id=batch_id, tenant_id=tenant_id, shop_id=shop_id,
                                  medicine_id=medicine_id, batch_no=f"ADJUSTMENT-{batch_id.hex[:8]}",
                                  expiry_date=date.max, quantity=0,
                                  purchase_cost=data.get("purchase_price", 0),
                                  selling_price=data.get("sale_price", 0))
            db.add(batch)
            db.flush()
        if batch is None or batch.quantity + delta < 0:
            raise ApiError(409, "offline_stock_conflict", "The stock adjustment conflicts with cloud inventory.")
        batch.quantity += delta
        db.add(StockMovement(tenant_id=tenant_id, shop_id=shop_id, batch_id=batch.id,
                             transaction_id=transaction_id, movement_type="offline_adjustment",
                             quantity_delta=delta, actor_id=actor_id))


@router.get("/changes")
def pull_changes(tenant_id: UUID, shop_id: UUID, cursor: int = 0, limit: int = 100,
                 x_device_id: UUID = Header(), x_device_secret: str = Header(min_length=40, max_length=200),
                 db: Session = Depends(get_db)):
    device = _authorized_device(db, tenant_id, shop_id, x_device_id, x_device_secret)
    rows = db.scalars(select(SyncEvent).where(
        SyncEvent.tenant_id == tenant_id, SyncEvent.shop_id == shop_id,
        SyncEvent.device_id != device.id, SyncEvent.status == "applied",
        SyncEvent.sequence > max(cursor, 0),
    ).order_by(SyncEvent.sequence).limit(min(max(limit, 1), 500))).all()
    changes = []
    for row in rows:
        event_payload = dict(row.payload)
        domain = dict(event_payload.get("payload", {}))
        domain.setdefault("shop_id", str(shop_id))
        domain.setdefault("occurred_at", row.created_at.isoformat())
        event_payload["payload"] = domain
        changes.append({"cursor": row.sequence, "event_id": str(row.event_id),
                        "event_type": row.event_type, "transaction_id": str(row.transaction_id),
                        "payload": event_payload, "created_at": row.created_at})
    return {"changes": changes, "cursor": changes[-1]["cursor"] if changes else cursor}


@router.get("/bootstrap")
def bootstrap(tenant_id: UUID, shop_id: UUID,
              x_device_id: UUID = Header(), x_device_secret: str = Header(min_length=40, max_length=200),
              db: Session = Depends(get_db)):
    """Initial authorized catalogue snapshot for an enrolled terminal."""
    _authorized_device(db, tenant_id, shop_id, x_device_id, x_device_secret)
    medicines = db.scalars(select(Medicine).where(Medicine.tenant_id == tenant_id, Medicine.is_active.is_(True))).all()
    batches = db.scalars(select(MedicineBatch).where(
        MedicineBatch.tenant_id == tenant_id, MedicineBatch.shop_id == shop_id,
    )).all()
    suppliers = db.scalars(select(Supplier).where(Supplier.tenant_id == tenant_id, Supplier.is_active.is_(True))).all()
    customers = db.scalars(select(Customer).where(Customer.tenant_id == tenant_id, Customer.is_active.is_(True))).all()
    return {
        "tenant_id": str(tenant_id), "shop_id": str(shop_id),
        "medicines": [{"id": str(m.id), "sku": m.sku, "name": m.name, "generic_name": m.generic_name,
                       "manufacturer": m.manufacturer, "barcode": m.barcode, "category": m.category,
                       "base_unit": m.base_unit, "is_controlled": m.is_controlled} for m in medicines],
        "batches": [{"id": str(b.id), "medicine_id": str(b.medicine_id), "batch_no": b.batch_no,
                     "expiry_date": b.expiry_date.isoformat(), "quantity": b.quantity,
                     "purchase_cost": str(b.purchase_cost), "selling_price": str(b.selling_price),
                     "supplier_id": str(b.supplier_id) if b.supplier_id else None} for b in batches],
        "suppliers": [{"id": str(s.id), "name": s.name, "phone": s.phone, "email": s.email,
                       "payable_balance": str(s.payable_balance)} for s in suppliers],
        "customers": [{"id": str(c.id), "name": c.name, "phone": c.phone,
                       "credit_balance": str(c.credit_balance)} for c in customers],
    }
