"""Tenant subscription billing and Safaricom callback endpoints."""
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import Principal, authorize_tenant, current_principal, require_platform_operator
from app.core.errors import ApiError
from app.core.rate_limit import limiter
from app.db.session import get_db, set_tenant_context
from app.models import (
    AuditEvent, AuthSession, Customer, DarajaAttempt, Device, Medicine, MedicineBatch,
    Payment, PlatformAuditEvent, Purchase, PurchaseItem, Sale, SaleItem, Shop,
    StockMovement, SubscriptionPayment, SubscriptionPlan, SupportAccessGrant,
    SupportIncident, Supplier, Tenant, TenantMembership, TenantSubscription,
    SyncEvent, User,
)
from app.schemas import (PlatformSubscriptionPatch, StkPushRequest, SubscriptionPlanCreate, SubscriptionPlanPatch,
                         SupportAccessRequest, SupportIncidentCreate)
from app.services import daraja
from app.services.audit import record_audit, record_platform_audit

billing_router = APIRouter(tags=["subscription billing"])
platform_router = APIRouter(prefix="/platform", tags=["platform administration"])


def _latest_subscription(db: Session, tenant_id: UUID):
    return db.scalar(select(TenantSubscription).where(TenantSubscription.tenant_id == tenant_id)
                     .order_by(TenantSubscription.ends_at.desc()).limit(1))


@billing_router.get("/tenants/{tenant_id}/billing")
def billing_summary(tenant_id: UUID, principal: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    authorize_tenant(db, principal, tenant_id, "billing:read")
    set_tenant_context(db, tenant_id)
    subscription = _latest_subscription(db, tenant_id)
    if subscription is None:
        raise ApiError(404, "subscription_not_found", "No subscription is configured for this tenant.")
    plan = db.scalar(select(SubscriptionPlan).where(SubscriptionPlan.id == subscription.plan_id))
    payments = db.scalars(select(SubscriptionPayment).where(SubscriptionPayment.tenant_id == tenant_id)
                          .order_by(SubscriptionPayment.created_at.desc()).limit(100)).all()
    attempts = db.scalars(select(DarajaAttempt).where(DarajaAttempt.tenant_id == tenant_id)
                          .order_by(DarajaAttempt.created_at.desc()).limit(100)).all()
    attempts_by_payment = {a.subscription_payment_id: a for a in attempts}
    return {
        "subscription": {"id": str(subscription.id), "status": subscription.status,
                         "starts_at": subscription.starts_at, "ends_at": subscription.ends_at,
                         "grace_until": subscription.grace_until,
                         "plan": {"id": str(plan.id), "code": plan.code, "name": plan.name,
                                  "monthly_price": str(plan.monthly_price), "max_shops": plan.max_shops,
                                  "max_devices_per_shop": plan.max_devices_per_shop}},
        "payments": [{"id": str(p.id), "amount": str(p.amount), "currency": p.currency,
                      "status": p.status, "created_at": p.created_at,
                      "mpesa_status": attempts_by_payment[p.id].status if p.id in attempts_by_payment else None,
                      "receipt": attempts_by_payment[p.id].mpesa_receipt if p.id in attempts_by_payment else None}
                     for p in payments],
    }


@billing_router.post("/tenants/{tenant_id}/billing/stk-push", status_code=202)
@limiter.limit("5/minute")
def start_subscription_payment(request: Request, tenant_id: UUID, payload: StkPushRequest,
                               principal: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    authorize_tenant(db, principal, tenant_id, "billing:manage")
    set_tenant_context(db, tenant_id)
    try:
        phone = daraja.normalize_phone(payload.phone_number)
    except ValueError as exc:
        raise ApiError(422, "invalid_phone", str(exc)) from None
    subscription = _latest_subscription(db, tenant_id)
    if subscription is None:
        raise ApiError(404, "subscription_not_found", "No subscription is configured for this tenant.")
    existing = db.scalar(select(SubscriptionPayment).where(
        SubscriptionPayment.tenant_id == tenant_id,
        SubscriptionPayment.idempotency_key == payload.idempotency_key,
    ))
    if existing:
        attempt = db.scalar(select(DarajaAttempt).where(
            DarajaAttempt.tenant_id == tenant_id,
            DarajaAttempt.subscription_payment_id == existing.id,
        ))
        return {"payment_id": str(existing.id), "status": existing.status,
                "checkout_request_id": attempt.checkout_request_id if attempt else None,
                "message": "This idempotency key was already submitted; no new payment prompt was created."}
    plan = db.scalar(select(SubscriptionPlan).where(SubscriptionPlan.id == subscription.plan_id))
    if plan is None or plan.monthly_price <= 0:
        raise ApiError(409, "plan_not_billable", "The current plan does not have a configured recurring price.")
    payment = SubscriptionPayment(
        tenant_id=tenant_id, subscription_id=subscription.id,
        idempotency_key=payload.idempotency_key, amount=plan.monthly_price,
        account_reference="SP" + secrets.token_hex(8).upper(), status="pending",
    )
    db.add(payment)
    db.flush()
    try:
        attempt = daraja.initiate_stk(db, tenant_id=tenant_id, subscription=subscription,
                                      payment=payment, phone_number=phone)
    except daraja.DarajaError as exc:
        attempt_exists = db.scalar(select(DarajaAttempt.id).where(
            DarajaAttempt.tenant_id == tenant_id,
            DarajaAttempt.subscription_payment_id == payment.id,
        ))
        if attempt_exists is None:
            payment.status = "failed"  # Configuration/OAuth failed before any STK request was sent.
        # A timeout is ambiguous: persist the attempt and force reconciliation.
        record_audit(db, tenant_id=tenant_id, actor_id=principal.user.id,
                     action="subscription.payment_initiation_uncertain", resource_type="subscription_payment",
                     resource_id=payment.id, details={"status": payment.status})
        return {"payment_id": str(payment.id), "status": payment.status,
                "message": str(exc), "reconciliation_required": attempt_exists is not None}
    record_audit(db, tenant_id=tenant_id, actor_id=principal.user.id,
                 action="subscription.payment_initiated", resource_type="subscription_payment",
                 resource_id=payment.id, details={"status": payment.status, "daraja_status": attempt.status})
    return {"payment_id": str(payment.id), "status": payment.status,
            "daraja_status": attempt.status, "checkout_request_id": attempt.checkout_request_id,
            "message": "Prompt submitted; payment is pending Safaricom confirmation."}


@billing_router.post("/payments/daraja/callback")
@limiter.limit("120/minute")
def daraja_callback(request: Request, payload: dict, db: Session = Depends(get_db)):
    try:
        return daraja.process_callback(db, payload)
    except ValueError:
        raise ApiError(422, "invalid_daraja_callback", "The callback payload is invalid.") from None


@billing_router.post("/tenants/{tenant_id}/billing/payments/{payment_id}/reconcile")
@limiter.limit("10/minute")
def reconcile_subscription_payment(request: Request, tenant_id: UUID, payment_id: UUID,
                                    principal: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    authorize_tenant(db, principal, tenant_id, "billing:manage")
    set_tenant_context(db, tenant_id)
    payment = db.scalar(select(SubscriptionPayment).where(
        SubscriptionPayment.tenant_id == tenant_id, SubscriptionPayment.id == payment_id,
    ))
    if payment is None:
        raise ApiError(404, "payment_not_found", "Subscription payment not found.")
    attempt = db.scalar(select(DarajaAttempt).where(
        DarajaAttempt.tenant_id == tenant_id,
        DarajaAttempt.subscription_payment_id == payment.id,
    ).with_for_update())
    if attempt is None:
        raise ApiError(409, "daraja_attempt_missing", "No M-Pesa attempt exists for this payment.")
    try:
        result = daraja.reconcile_stk_status(db, attempt)
    except daraja.DarajaError as exc:
        raise ApiError(503, "daraja_reconciliation_unavailable", str(exc)) from None
    record_audit(db, tenant_id=tenant_id, actor_id=principal.user.id,
                 action="subscription.payment_reconciled", resource_type="subscription_payment",
                 resource_id=payment.id, details={"status": attempt.status})
    return {"payment_id": str(payment.id), "payment_status": payment.status, **result}


@billing_router.post("/tenants/{tenant_id}/support-incidents", status_code=201)
@limiter.limit("5/hour")
def create_support_incident(request: Request, tenant_id: UUID, payload: SupportIncidentCreate,
                            principal: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    authorize_tenant(db, principal, tenant_id, "tenant:read")
    incident = SupportIncident(tenant_id=tenant_id, created_by=principal.user.id,
                               subject=payload.subject.strip(), description=payload.description.strip())
    db.add(incident)
    db.flush()
    record_audit(db, tenant_id=tenant_id, actor_id=principal.user.id, action="support.incident_created",
                 resource_type="support_incident", resource_id=incident.id)
    return {"id": str(incident.id), "status": incident.status, "created_at": incident.created_at}


@billing_router.get("/tenants/{tenant_id}/support-incidents")
def list_support_incidents(tenant_id: UUID, principal: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    authorize_tenant(db, principal, tenant_id, "tenant:read")
    rows = db.scalars(select(SupportIncident).where(SupportIncident.tenant_id == tenant_id)
                      .order_by(SupportIncident.created_at.desc()).limit(100)).all()
    return [{"id": str(row.id), "subject": row.subject, "status": row.status,
             "created_at": row.created_at} for row in rows]


@billing_router.get("/tenants/{tenant_id}/export")
def export_tenant_data(tenant_id: UUID, principal: Principal = Depends(current_principal),
                       db: Session = Depends(get_db)):
    membership = authorize_tenant(db, principal, tenant_id, "tenant:manage")
    if not membership.all_shops:
        raise ApiError(403, "tenant_wide_export_required", "Tenant-wide data export requires access to all shops.")
    set_tenant_context(db, tenant_id)
    models = {
        "tenant": Tenant, "shops": Shop, "memberships": TenantMembership,
        "medicines": Medicine, "batches": MedicineBatch, "suppliers": Supplier,
        "customers": Customer, "purchases": Purchase, "purchase_items": PurchaseItem,
        "sales": Sale, "sale_items": SaleItem, "payments": Payment,
        "stock_movements": StockMovement, "audit_events": AuditEvent,
        "subscription": TenantSubscription, "subscription_payments": SubscriptionPayment,
    }
    export = {"tenant_id": str(tenant_id), "exported_at": datetime.now(timezone.utc).isoformat(), "records": {}}
    for name, model in models.items():
        if name == "tenant":
            statement = select(model).where(model.id == tenant_id)
        else:
            statement = select(model).where(model.tenant_id == tenant_id)
        order_column = getattr(model, "created_at", getattr(model, "occurred_at", model.id))
        rows = db.scalars(statement.order_by(order_column).limit(100000)).all()
        fields = [column.key for column in model.__table__.columns
                  if column.key not in {"credential_hash", "password_hash", "token_hash"}]
        export["records"][name] = [{field: getattr(row, field) for field in fields} for row in rows]
    member_ids = select(TenantMembership.user_id).where(TenantMembership.tenant_id == tenant_id)
    users = db.scalars(select(User).where(User.id.in_(member_ids)).order_by(User.email).limit(100000)).all()
    export["records"]["users"] = [{"id": row.id, "email": row.email, "full_name": row.full_name,
                                    "is_active": row.is_active, "created_at": row.created_at}
                                   for row in users]
    record_audit(db, tenant_id=tenant_id, actor_id=principal.user.id,
                 action="tenant.data_exported", resource_type="tenant", resource_id=tenant_id)
    return JSONResponse(jsonable_encoder(export), headers={
        "Content-Disposition": f'attachment; filename="tenant-{tenant_id}-export.json"',
        "Cache-Control": "no-store",
    })


@platform_router.get("/tenants")
def platform_tenants(_: Principal = Depends(require_platform_operator), db: Session = Depends(get_db)):
    rows = db.execute(select(Tenant, TenantSubscription, SubscriptionPlan)
                      .outerjoin(TenantSubscription, TenantSubscription.tenant_id == Tenant.id)
                      .outerjoin(SubscriptionPlan, SubscriptionPlan.id == TenantSubscription.plan_id)
                      .order_by(Tenant.created_at.desc()).limit(500)).all()
    return [{"tenant_id": str(t.id), "name": t.name, "slug": t.slug, "is_active": t.is_active,
             "subscription_id": str(s.id) if s else None, "subscription_status": s.status if s else "unconfigured",
             "ends_at": s.ends_at if s else None, "grace_until": s.grace_until if s else None,
             "plan": plan.code if plan else None} for t, s, plan in rows]


@platform_router.patch("/tenants/{tenant_id}/subscription")
def platform_set_subscription(tenant_id: UUID, payload: PlatformSubscriptionPatch,
                              principal: Principal = Depends(require_platform_operator), db: Session = Depends(get_db)):
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise ApiError(404, "tenant_not_found", "Tenant not found.")
    subscription = _latest_subscription(db, tenant_id)
    if subscription is None:
        raise ApiError(404, "subscription_not_found", "Subscription not found.")
    if payload.plan_id is not None:
        plan = db.scalar(select(SubscriptionPlan).where(SubscriptionPlan.id == payload.plan_id,
                                                        SubscriptionPlan.is_active.is_(True)))
        if plan is None:
            raise ApiError(404, "plan_not_found", "Active subscription plan not found.")
        subscription.plan_id = plan.id
    now = datetime.now(timezone.utc)
    subscription.status = payload.status
    if payload.end_days is not None:
        subscription.ends_at = now + timedelta(days=payload.end_days)
    subscription.grace_until = now + timedelta(days=payload.grace_days) if payload.grace_days else None
    set_tenant_context(db, tenant_id)
    record_audit(db, tenant_id=tenant_id, actor_id=principal.user.id,
                 action="platform.subscription_changed", resource_type="subscription",
                 resource_id=subscription.id, details={"status": payload.status,
                                                       "grace_days": payload.grace_days,
                                                       "end_days": payload.end_days,
                                                       "plan_id": str(subscription.plan_id)})
    record_platform_audit(db, actor_id=principal.user.id, tenant_id=tenant_id,
                          action="subscription.changed", resource_type="subscription",
                          resource_id=subscription.id, details={"status": payload.status,
                                                               "grace_days": payload.grace_days,
                                                               "end_days": payload.end_days,
                                                               "plan_id": str(subscription.plan_id)})
    return {"tenant_id": str(tenant_id), "subscription_id": str(subscription.id),
            "status": subscription.status, "ends_at": subscription.ends_at,
            "grace_until": subscription.grace_until}


@platform_router.get("/billing/payments")
def platform_billing_payments(_: Principal = Depends(require_platform_operator), db: Session = Depends(get_db)):
    rows = db.execute(select(SubscriptionPayment, DarajaAttempt)
                      .outerjoin(DarajaAttempt, DarajaAttempt.subscription_payment_id == SubscriptionPayment.id)
                      .order_by(SubscriptionPayment.created_at.desc()).limit(500)).all()
    return [{"tenant_id": str(payment.tenant_id), "payment_id": str(payment.id),
             "amount": str(payment.amount), "status": payment.status,
             "daraja_status": attempt.status if attempt else None,
             "receipt": attempt.mpesa_receipt if attempt else None,
             "created_at": payment.created_at} for payment, attempt in rows]


@platform_router.get("/subscription-plans")
def platform_plans(_: Principal = Depends(require_platform_operator), db: Session = Depends(get_db)):
    rows = db.scalars(select(SubscriptionPlan).order_by(SubscriptionPlan.code)).all()
    return [{"id": str(plan.id), "code": plan.code, "name": plan.name,
             "max_shops": plan.max_shops, "max_devices_per_shop": plan.max_devices_per_shop,
             "monthly_price": str(plan.monthly_price), "trial_days": plan.trial_days,
             "features": plan.features, "is_active": plan.is_active} for plan in rows]


@platform_router.post("/subscription-plans", status_code=201)
def create_plan(payload: SubscriptionPlanCreate, principal: Principal = Depends(require_platform_operator),
                db: Session = Depends(get_db)):
    if db.scalar(select(SubscriptionPlan.id).where(SubscriptionPlan.code == payload.code)):
        raise ApiError(409, "plan_code_exists", "A subscription plan already uses this code.")
    plan = SubscriptionPlan(**payload.model_dump())
    db.add(plan)
    db.flush()
    record_platform_audit(db, actor_id=principal.user.id, action="subscription_plan.created",
                          resource_type="subscription_plan", resource_id=plan.id,
                          details={"code": plan.code, "monthly_price": str(plan.monthly_price)})
    return {"id": str(plan.id), "code": plan.code, "name": plan.name}


@platform_router.patch("/subscription-plans/{plan_id}")
def update_plan(plan_id: UUID, payload: SubscriptionPlanPatch,
                principal: Principal = Depends(require_platform_operator), db: Session = Depends(get_db)):
    plan = db.get(SubscriptionPlan, plan_id)
    if plan is None:
        raise ApiError(404, "plan_not_found", "Subscription plan not found.")
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(plan, field, value)
    record_platform_audit(db, actor_id=principal.user.id, action="subscription_plan.updated",
                          resource_type="subscription_plan", resource_id=plan.id,
                          details={"fields": sorted(changes)})
    return {"id": str(plan.id), "code": plan.code, "name": plan.name,
            "monthly_price": str(plan.monthly_price), "is_active": plan.is_active}


@platform_router.post("/users/{user_id}/revoke-sessions")
def platform_revoke_sessions(user_id: UUID, principal: Principal = Depends(require_platform_operator),
                             db: Session = Depends(get_db)):
    if db.get(User, user_id) is None:
        raise ApiError(404, "user_not_found", "User not found.")
    now = datetime.now(timezone.utc)
    sessions = db.scalars(select(AuthSession).where(AuthSession.user_id == user_id,
                                                      AuthSession.revoked_at.is_(None)).with_for_update()).all()
    for session in sessions:
        session.revoked_at = now
    record_platform_audit(db, actor_id=principal.user.id, action="account.sessions_revoked",
                          resource_type="user", resource_id=user_id,
                          details={"sessions_revoked": len(sessions)})
    return {"user_id": str(user_id), "sessions_revoked": len(sessions)}


@platform_router.get("/support-incidents")
def platform_incidents(_: Principal = Depends(require_platform_operator), db: Session = Depends(get_db)):
    rows = db.scalars(select(SupportIncident).order_by(SupportIncident.created_at.desc()).limit(500)).all()
    return [{"id": str(row.id), "tenant_id": str(row.tenant_id), "subject": row.subject,
             "status": row.status, "created_at": row.created_at} for row in rows]


@platform_router.patch("/support-incidents/{incident_id}")
def platform_update_incident(incident_id: UUID, status: str,
                             principal: Principal = Depends(require_platform_operator), db: Session = Depends(get_db)):
    if status not in {"open", "investigating", "waiting_on_tenant", "resolved", "closed"}:
        raise ApiError(422, "invalid_incident_status", "The requested support incident status is invalid.")
    incident = db.get(SupportIncident, incident_id)
    if incident is None:
        raise ApiError(404, "incident_not_found", "Support incident not found.")
    incident.status = status
    record_platform_audit(db, actor_id=principal.user.id, tenant_id=incident.tenant_id,
                          action="support_incident.status_changed", resource_type="support_incident",
                          resource_id=incident.id, details={"status": status})
    return {"id": str(incident.id), "tenant_id": str(incident.tenant_id), "status": incident.status}


@platform_router.get("/devices")
def platform_devices(tenant_id: UUID | None = None,
                     _: Principal = Depends(require_platform_operator), db: Session = Depends(get_db)):
    query = select(Device, Shop.name).join(Shop, Shop.id == Device.shop_id)
    if tenant_id is not None:
        query = query.where(Device.tenant_id == tenant_id)
    rows = db.execute(query.order_by(Device.created_at.desc()).limit(1000)).all()
    return [{"id": str(device.id), "tenant_id": str(device.tenant_id),
             "shop_id": str(device.shop_id), "shop_name": shop_name,
             "name": device.name, "platform": device.platform,
             "revoked_at": device.revoked_at} for device, shop_name in rows]


@platform_router.delete("/devices/{device_id}")
def platform_revoke_device(device_id: UUID, principal: Principal = Depends(require_platform_operator),
                           db: Session = Depends(get_db)):
    device = db.get(Device, device_id)
    if device is None:
        raise ApiError(404, "device_not_found", "Device not found.")
    if device.revoked_at is None:
        device.revoked_at = datetime.now(timezone.utc)
    set_tenant_context(db, device.tenant_id)
    record_audit(db, tenant_id=device.tenant_id, actor_id=principal.user.id,
                 action="platform.device_revoked", resource_type="device",
                 resource_id=device.id, shop_id=device.shop_id)
    record_platform_audit(db, actor_id=principal.user.id, tenant_id=device.tenant_id,
                          action="device.revoked", resource_type="device", resource_id=device.id)
    return {"status": "revoked"}


@platform_router.get("/synchronization/health")
def platform_sync_health(_: Principal = Depends(require_platform_operator), db: Session = Depends(get_db)):
    rows = db.execute(select(SyncEvent.status, func.count(SyncEvent.id))
                      .group_by(SyncEvent.status).order_by(SyncEvent.status)).all()
    return {"event_counts_by_status": {status: count for status, count in rows},
            "review_required": sum(count for status, count in rows if status in {"conflict", "needs_review"})}


@platform_router.get("/audit")
def platform_audit(limit: int = 100, _: Principal = Depends(require_platform_operator),
                   db: Session = Depends(get_db)):
    rows = db.scalars(select(PlatformAuditEvent).order_by(PlatformAuditEvent.created_at.desc())
                      .limit(min(max(limit, 1), 500))).all()
    return [{"id": str(row.id), "actor_id": str(row.actor_id),
             "tenant_id": str(row.tenant_id) if row.tenant_id else None,
             "action": row.action, "resource_type": row.resource_type,
             "resource_id": str(row.resource_id) if row.resource_id else None,
             "created_at": row.created_at} for row in rows]


@platform_router.post("/tenants/{tenant_id}/support-access-grants", status_code=201)
def grant_support_access(tenant_id: UUID, payload: SupportAccessRequest,
                         principal: Principal = Depends(require_platform_operator), db: Session = Depends(get_db)):
    if db.get(Tenant, tenant_id) is None:
        raise ApiError(404, "tenant_not_found", "Tenant not found.")
    # This grant permits metadata audit inspection only; no business records are
    # exposed through support-access endpoints.
    now = datetime.now(timezone.utc)
    grant = SupportAccessGrant(tenant_id=tenant_id, operator_id=principal.user.id,
                              reason=payload.reason.strip(), granted_at=now,
                              expires_at=now + timedelta(minutes=min(payload.duration_minutes, 15)))
    db.add(grant)
    db.flush()
    set_tenant_context(db, tenant_id)
    record_audit(db, tenant_id=tenant_id, actor_id=principal.user.id,
                 action="platform.support_access_granted", resource_type="support_access_grant",
                 resource_id=grant.id, details={"reason": grant.reason,
                                                "expires_at": grant.expires_at.isoformat()})
    record_platform_audit(db, actor_id=principal.user.id, tenant_id=tenant_id,
                          action="support_access.granted", resource_type="support_access_grant",
                          resource_id=grant.id, details={"reason": grant.reason,
                                                       "expires_at": grant.expires_at.isoformat()})
    return {"id": str(grant.id), "tenant_id": str(tenant_id), "expires_at": grant.expires_at,
            "scope": "metadata_only; no tenant clinical or commercial data endpoint is enabled"}


@platform_router.get("/tenants/{tenant_id}/audit")
def platform_tenant_audit(tenant_id: UUID, limit: int = 100,
                          principal: Principal = Depends(require_platform_operator), db: Session = Depends(get_db)):
    grant = db.scalar(select(SupportAccessGrant).where(
        SupportAccessGrant.tenant_id == tenant_id,
        SupportAccessGrant.operator_id == principal.user.id,
        SupportAccessGrant.revoked_at.is_(None),
        SupportAccessGrant.expires_at > datetime.now(timezone.utc),
    ).order_by(SupportAccessGrant.expires_at.desc()).limit(1))
    if grant is None:
        raise ApiError(403, "support_access_required", "A time-limited, reasoned support access grant is required.")
    set_tenant_context(db, tenant_id)
    rows = db.scalars(select(AuditEvent).where(AuditEvent.tenant_id == tenant_id)
                      .order_by(AuditEvent.occurred_at.desc()).limit(min(max(limit, 1), 500))).all()
    record_audit(db, tenant_id=tenant_id, actor_id=principal.user.id,
                 action="platform.support_audit_viewed", resource_type="support_access_grant",
                 resource_id=grant.id, details={"row_count": len(rows), "reason": grant.reason})
    record_platform_audit(db, actor_id=principal.user.id, tenant_id=tenant_id,
                          action="support_access.audit_viewed", resource_type="support_access_grant",
                          resource_id=grant.id, details={"row_count": len(rows)})
    # Omit free-form details to avoid unnecessary exposure of customer or health data.
    return [{"id": str(row.id), "actor_id": str(row.actor_id) if row.actor_id else None,
             "action": row.action, "resource_type": row.resource_type,
             "occurred_at": row.occurred_at} for row in rows]


@platform_router.delete("/support-access-grants/{grant_id}")
def revoke_support_grant(grant_id: UUID, principal: Principal = Depends(require_platform_operator),
                         db: Session = Depends(get_db)):
    grant = db.get(SupportAccessGrant, grant_id)
    if grant is None:
        raise ApiError(404, "support_grant_not_found", "Support access grant not found.")
    grant.revoked_at = datetime.now(timezone.utc)
    set_tenant_context(db, grant.tenant_id)
    record_audit(db, tenant_id=grant.tenant_id, actor_id=principal.user.id,
                 action="platform.support_access_revoked", resource_type="support_access_grant",
                 resource_id=grant.id, details={})
    record_platform_audit(db, actor_id=principal.user.id, tenant_id=grant.tenant_id,
                          action="support_access.revoked", resource_type="support_access_grant",
                          resource_id=grant.id)
    return {"status": "revoked"}
