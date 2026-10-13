from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Principal, authorize_shop, authorize_tenant, current_principal, require_subscription
from app.core.errors import ApiError
from app.db.session import get_db
from app.models import Medicine, MedicineBatch
from app.schemas import CustomerCreate, MedicineCreate, PaymentCreate, PurchaseCreate, SaleCreate, SupplierCreate
from app.services import pharmacy as pharmacy_service

router = APIRouter(tags=["pharmacy operations"])


def assert_write_access(db: Session, principal: Principal, tenant_id: UUID, shop_id: UUID, permission: str) -> None:
    membership = authorize_tenant(db, principal, tenant_id, permission)
    authorize_shop(db, membership, shop_id)
    require_subscription(db, tenant_id)


@router.get("/tenants/{tenant_id}/medicines")
def list_medicines(tenant_id: UUID, principal: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    authorize_tenant(db, principal, tenant_id, "inventory:read")
    rows = db.scalars(select(Medicine).where(Medicine.tenant_id == tenant_id, Medicine.is_active.is_(True)).order_by(Medicine.name)).all()
    return [{"id": str(m.id), "sku": m.sku, "name": m.name, "generic_name": m.generic_name, "manufacturer": m.manufacturer, "barcode": m.barcode, "category": m.category, "base_unit": m.base_unit, "is_controlled": m.is_controlled} for m in rows]


@router.post("/tenants/{tenant_id}/medicines", status_code=201)
def create_medicine(tenant_id: UUID, payload: MedicineCreate, principal: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    authorize_tenant(db, principal, tenant_id, "inventory:write")
    require_subscription(db, tenant_id)
    medicine = pharmacy_service.add_medicine(db, tenant_id, principal.user.id, payload)
    return {"id": str(medicine.id), "sku": medicine.sku, "name": medicine.name}


@router.get("/tenants/{tenant_id}/shops/{shop_id}/batches")
def list_batches(tenant_id: UUID, shop_id: UUID, principal: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    membership = authorize_tenant(db, principal, tenant_id, "inventory:read")
    authorize_shop(db, membership, shop_id)
    batches = db.execute(
        select(MedicineBatch, Medicine.name, Medicine.sku)
        .join(Medicine, Medicine.id == MedicineBatch.medicine_id)
        .where(MedicineBatch.tenant_id == tenant_id, MedicineBatch.shop_id == shop_id)
        .order_by(Medicine.name, MedicineBatch.expiry_date)
    ).all()
    return [{"id": str(b.id), "medicine_id": str(b.medicine_id), "sku": sku, "medicine_name": name, "batch_no": b.batch_no, "expiry_date": b.expiry_date, "quantity": b.quantity, "purchase_cost": b.purchase_cost, "selling_price": b.selling_price} for b, name, sku in batches]


@router.get("/tenants/{tenant_id}/shops/{shop_id}/inventory/alerts")
def inventory_alerts(tenant_id: UUID, shop_id: UUID,
                     within_days: int = Query(default=90, ge=1, le=365),
                     low_stock_threshold: int = Query(default=10, ge=0, le=100000),
                     principal: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    membership = authorize_tenant(db, principal, tenant_id, "inventory:read")
    authorize_shop(db, membership, shop_id)
    from app.models import Shop

    shop = db.get(Shop, shop_id)
    today = datetime.now(ZoneInfo(shop.timezone)).date()
    rows = db.execute(select(MedicineBatch, Medicine.name, Medicine.sku).join(
        Medicine, Medicine.id == MedicineBatch.medicine_id).where(
        MedicineBatch.tenant_id == tenant_id, MedicineBatch.shop_id == shop_id,
        MedicineBatch.quantity > 0).order_by(MedicineBatch.expiry_date, Medicine.name)).all()
    expiring = []
    expired = []
    totals = {}
    for batch, medicine_name, sku in rows:
        totals[batch.medicine_id] = totals.get(batch.medicine_id, 0) + batch.quantity
        result = {"batch_id": str(batch.id), "medicine_id": str(batch.medicine_id),
                  "medicine_name": medicine_name, "sku": sku, "batch_no": batch.batch_no,
                  "expiry_date": batch.expiry_date.isoformat(), "quantity": batch.quantity}
        days_left = (batch.expiry_date - today).days
        if days_left < 0:
            expired.append(result)
        elif days_left <= within_days:
            expiring.append({**result, "days_until_expiry": days_left})
    medicines = db.scalars(select(Medicine).where(Medicine.tenant_id == tenant_id, Medicine.is_active.is_(True))).all()
    low_stock = [{"medicine_id": str(medicine.id), "name": medicine.name,
                  "sku": medicine.sku, "quantity": totals.get(medicine.id, 0)}
                 for medicine in medicines if totals.get(medicine.id, 0) <= low_stock_threshold]
    return {"as_of": today.isoformat(), "expired_batches": expired,
            "expiring_batches": expiring, "low_stock_medicines": low_stock}


@router.post("/tenants/{tenant_id}/suppliers", status_code=201)
def create_supplier(tenant_id: UUID, payload: SupplierCreate, principal: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    authorize_tenant(db, principal, tenant_id, "inventory:write")
    require_subscription(db, tenant_id)
    supplier = pharmacy_service.add_supplier(db, tenant_id, principal.user.id, payload)
    return {"id": str(supplier.id), "name": supplier.name}


@router.post("/tenants/{tenant_id}/customers", status_code=201)
def create_customer(tenant_id: UUID, payload: CustomerCreate, principal: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    authorize_tenant(db, principal, tenant_id, "sales:write")
    require_subscription(db, tenant_id)
    customer = pharmacy_service.add_customer(db, tenant_id, principal.user.id, payload)
    return {"id": str(customer.id), "name": customer.name}


@router.post("/tenants/{tenant_id}/purchases", status_code=201)
def receive_purchase(tenant_id: UUID, payload: PurchaseCreate, principal: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    assert_write_access(db, principal, tenant_id, payload.shop_id, "inventory:write")
    purchase = pharmacy_service.receive_purchase(db, tenant_id, payload.shop_id, principal.user.id, payload)
    return {"id": str(purchase.id), "transaction_id": str(purchase.transaction_id), "total_amount": purchase.total_amount, "amount_paid": purchase.amount_paid, "payable_added": purchase.total_amount - purchase.amount_paid, "status": purchase.status}


@router.post("/tenants/{tenant_id}/sales", status_code=201)
def create_sale(tenant_id: UUID, payload: SaleCreate, principal: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    assert_write_access(db, principal, tenant_id, payload.shop_id, "sales:write")
    sale = pharmacy_service.record_sale(db, tenant_id, payload.shop_id, principal.user.id, payload)
    return {"id": str(sale.id), "invoice_no": sale.invoice_no, "transaction_id": str(sale.transaction_id), "total_amount": sale.total_amount, "amount_paid": sale.amount_paid, "balance_due": pharmacy_service.money(sale.total_amount - sale.amount_paid), "status": sale.status}


@router.post("/tenants/{tenant_id}/shops/{shop_id}/sales/{sale_id}/payments", status_code=201)
def add_sale_payment(tenant_id: UUID, shop_id: UUID, sale_id: UUID, payload: PaymentCreate, principal: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    assert_write_access(db, principal, tenant_id, shop_id, "sales:write")
    payment, sale = pharmacy_service.record_sale_payment(db, tenant_id, shop_id, sale_id, principal.user.id, payload)
    return {"id": str(payment.id), "sale_id": str(sale.id), "amount_paid": sale.amount_paid, "balance_due": pharmacy_service.money(sale.total_amount - sale.amount_paid), "status": sale.status}


@router.post("/tenants/{tenant_id}/shops/{shop_id}/purchases/{purchase_id}/payments", status_code=201)
def add_purchase_payment(tenant_id: UUID, shop_id: UUID, purchase_id: UUID, payload: PaymentCreate, principal: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    assert_write_access(db, principal, tenant_id, shop_id, "inventory:write")
    payment, purchase = pharmacy_service.record_purchase_payment(db, tenant_id, shop_id, purchase_id, principal.user.id, payload)
    return {"id": str(payment.id), "purchase_id": str(purchase.id), "amount_paid": purchase.amount_paid, "balance_due": pharmacy_service.money(purchase.total_amount - purchase.amount_paid), "status": purchase.status}
