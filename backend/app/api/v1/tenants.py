from datetime import datetime, timedelta, timezone
import secrets
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import Principal, authorize_shop, authorize_tenant, current_plan, require_subscription, current_principal
from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.rbac import assert_can_modify_membership, lock_tenant_memberships
from app.core.rate_limit import limiter
from app.core.security import hash_token
from app.db.session import get_db, set_tenant_context
from app.models import (
    Device, MedicineBatch, Permission, Role, RolePermission, Shop, ShopAssignment,
    SubscriptionPlan, Tenant, TenantMembership, TenantSubscription, User,
    UserInvitation, Sale,
)
from app.schemas import (
    DeviceCreate, InvitationCreate, OkResponse, ShopCreate, ShopPatch,
    SummaryResponse, TenantPatch,
    MembershipRolePatch,
)
from app.services.audit import record_audit

router = APIRouter(tags=["tenant and shop management"])


def _check_membership_policy(db: Session, *, tenant_id: UUID, actor_id: UUID,
                             actor_role: str | None, target_id: UUID | None,
                             target_current_role: str | None, target_new_role: str | None,
                             operation: str) -> None:
    try:
        assert_can_modify_membership(actor_role, target_current_role, target_new_role)
    except ApiError as error:
        record_audit(
            db, tenant_id=tenant_id, actor_id=actor_id, action="staff.permission_denied",
            resource_type="membership", resource_id=target_id,
            details={"operation": operation, "actor_role": actor_role,
                     "target_current_role": target_current_role,
                     "attempted_role": target_new_role},
        )
        # Preserve the denied-attempt audit even though the request raises.
        db.commit()
        raise error


@router.get("/tenants/{tenant_id}")
def get_tenant(tenant_id: UUID, principal: Principal = Depends(current_principal), db: Session = Depends(get_db, scope="function")):
    authorize_tenant(db, principal, tenant_id, "tenant:read")
    tenant = db.get(Tenant, tenant_id)
    return {"id": str(tenant.id), "name": tenant.name, "slug": tenant.slug, "business_details": tenant.business_details}


@router.patch("/tenants/{tenant_id}")
def update_tenant(tenant_id: UUID, payload: TenantPatch, principal: Principal = Depends(current_principal), db: Session = Depends(get_db, scope="function")):
    authorize_tenant(db, principal, tenant_id, "tenant:manage")
    tenant = db.get(Tenant, tenant_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(tenant, key, value)
    record_audit(db, tenant_id=tenant_id, actor_id=principal.user.id, action="tenant.updated", resource_type="tenant", resource_id=tenant_id)
    return {"id": str(tenant.id), "name": tenant.name, "slug": tenant.slug, "business_details": tenant.business_details}


@router.get("/tenants/{tenant_id}/shops")
def list_shops(tenant_id: UUID, principal: Principal = Depends(current_principal), db: Session = Depends(get_db, scope="function")):
    membership = authorize_tenant(db, principal, tenant_id, "tenant:read")
    query = select(Shop).where(Shop.tenant_id == tenant_id, Shop.is_active.is_(True))
    if not membership.all_shops:
        query = query.join(ShopAssignment, ShopAssignment.shop_id == Shop.id).where(ShopAssignment.membership_id == membership.id)
    shops = db.scalars(query.order_by(Shop.name)).all()
    return [{"id": str(s.id), "name": s.name, "code": s.code, "timezone": s.timezone, "receipt_settings": s.receipt_settings} for s in shops]


@router.post("/tenants/{tenant_id}/shops", status_code=201)
def create_shop(tenant_id: UUID, payload: ShopCreate, principal: Principal = Depends(current_principal), db: Session = Depends(get_db, scope="function")):
    authorize_tenant(db, principal, tenant_id, "tenant:manage")
    require_subscription(db, tenant_id)
    plan = current_plan(db, tenant_id)
    count = db.scalar(select(func.count(Shop.id)).where(Shop.tenant_id == tenant_id)) or 0
    if plan is None or count >= plan.max_shops:
        raise ApiError(403, "shop_limit_reached", "The active plan does not allow another shop.")
    shop = Shop(tenant_id=tenant_id, name=payload.name.strip(), code=payload.code, timezone=payload.timezone, receipt_settings=payload.receipt_settings)
    db.add(shop)
    db.flush()
    record_audit(db, tenant_id=tenant_id, actor_id=principal.user.id, action="shop.created", resource_type="shop", resource_id=shop.id, shop_id=shop.id)
    return {"id": str(shop.id), "name": shop.name, "code": shop.code, "timezone": shop.timezone, "receipt_settings": shop.receipt_settings}


@router.patch("/tenants/{tenant_id}/shops/{shop_id}")
def update_shop(tenant_id: UUID, shop_id: UUID, payload: ShopPatch, principal: Principal = Depends(current_principal), db: Session = Depends(get_db, scope="function")):
    membership = authorize_tenant(db, principal, tenant_id, "tenant:manage")
    authorize_shop(db, membership, shop_id, allow_inactive=True)
    shop = db.get(Shop, shop_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(shop, key, value)
    record_audit(db, tenant_id=tenant_id, actor_id=principal.user.id, action="shop.updated", resource_type="shop", resource_id=shop_id, shop_id=shop_id)
    return {"id": str(shop.id), "name": shop.name, "code": shop.code, "timezone": shop.timezone, "receipt_settings": shop.receipt_settings, "is_active": shop.is_active}


@router.post("/tenants/{tenant_id}/invitations", status_code=201)
@limiter.limit("20/hour")
def invite_user(request: Request, tenant_id: UUID, payload: InvitationCreate, principal: Principal = Depends(current_principal), db: Session = Depends(get_db, scope="function")):
    actor_membership = authorize_tenant(db, principal, tenant_id, "staff:manage")
    lock_tenant_memberships(db, tenant_id)
    actor_role = db.get(Role, actor_membership.role_id)
    role = db.scalar(select(Role).where(Role.tenant_id == tenant_id, Role.name == payload.role))
    if role is None:
        raise ApiError(404, "role_not_found", "Role not found.")
    if payload.email.casefold() == principal.user.email.casefold():
        _check_membership_policy(db, tenant_id=tenant_id, actor_id=principal.user.id,
                                 actor_role=actor_role.name if actor_role else None,
                                 target_id=None, target_current_role=None,
                                 target_new_role=role.name, operation="invite_self")
        raise ApiError(409, "cannot_invite_self", "You cannot invite yourself to another membership.")
    _check_membership_policy(db, tenant_id=tenant_id, actor_id=principal.user.id,
                             actor_role=actor_role.name if actor_role else None,
                             target_id=None, target_current_role=None,
                             target_new_role=role.name, operation="invite")
    if len(payload.shop_ids) != len(set(payload.shop_ids)):
        raise ApiError(422, "invalid_shop_assignment", "A shop may only be assigned once.")
    for shop_id in payload.shop_ids:
        if db.scalar(select(Shop.id).where(Shop.tenant_id == tenant_id, Shop.id == shop_id, Shop.is_active.is_(True))) is None:
            raise ApiError(422, "invalid_shop_assignment", "One or more shops are not available in this tenant.")
    if db.scalar(select(TenantMembership.id).join(User, User.id == TenantMembership.user_id).where(TenantMembership.tenant_id == tenant_id, User.email == payload.email, TenantMembership.is_active.is_(True))):
        raise ApiError(409, "membership_exists", "That user already belongs to this tenant.")
    raw_token = f"{tenant_id}.{secrets.token_urlsafe(48)}"
    invitation = UserInvitation(
        tenant_id=tenant_id,
        email=payload.email,
        role_id=role.id,
        shop_ids=[str(value) for value in payload.shop_ids],
        token_hash=hash_token(raw_token),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=get_settings().invitation_hours),
        created_by=principal.user.id,
    )
    db.add(invitation)
    db.flush()
    record_audit(db, tenant_id=tenant_id, actor_id=principal.user.id, action="staff.invited", resource_type="invitation", resource_id=invitation.id)
    # No email provider exists yet. This one-time credential must be delivered
    # by the authorized tenant administrator through a secure channel.
    return {"id": str(invitation.id), "email": invitation.email, "expires_at": invitation.expires_at, "invitation_token": raw_token}


@router.get("/tenants/{tenant_id}/roles")
def list_roles(tenant_id: UUID, principal: Principal = Depends(current_principal), db: Session = Depends(get_db, scope="function")):
    authorize_tenant(db, principal, tenant_id, "staff:manage")
    rows = db.execute(
        select(Role, Permission.code)
        .outerjoin(RolePermission, RolePermission.role_id == Role.id)
        .outerjoin(Permission, Permission.id == RolePermission.permission_id)
        .where(Role.tenant_id == tenant_id)
        .order_by(Role.name, Permission.code)
    ).all()
    result: dict[str, dict] = {}
    for role, code in rows:
        result.setdefault(str(role.id), {"id": str(role.id), "name": role.name, "permissions": []})
        if code:
            result[str(role.id)]["permissions"].append(code)
    return list(result.values())


@router.get("/tenants/{tenant_id}/memberships")
def list_memberships(tenant_id: UUID, principal: Principal = Depends(current_principal), db: Session = Depends(get_db, scope="function")):
    authorize_tenant(db, principal, tenant_id, "staff:manage")
    rows = db.execute(
        select(TenantMembership, User, Role)
        .join(User, User.id == TenantMembership.user_id)
        .join(Role, Role.id == TenantMembership.role_id)
        .where(TenantMembership.tenant_id == tenant_id)
        .order_by(User.email)
    ).all()
    assignments = db.scalars(select(ShopAssignment).where(ShopAssignment.tenant_id == tenant_id)).all()
    shops_by_membership: dict[UUID, list[str]] = {}
    for assignment in assignments:
        shops_by_membership.setdefault(assignment.membership_id, []).append(str(assignment.shop_id))
    return [{"id": str(m.id), "user_id": str(user.id), "email": user.email, "full_name": user.full_name, "role": role.name, "is_active": m.is_active, "all_shops": m.all_shops, "shop_ids": shops_by_membership.get(m.id, [])} for m, user, role in rows]


@router.patch("/tenants/{tenant_id}/memberships/{membership_id}")
def deactivate_membership(tenant_id: UUID, membership_id: UUID, principal: Principal = Depends(current_principal), db: Session = Depends(get_db, scope="function")):
    actor_membership = authorize_tenant(db, principal, tenant_id, "staff:manage")
    lock_tenant_memberships(db, tenant_id)
    membership = db.scalar(select(TenantMembership).where(TenantMembership.tenant_id == tenant_id, TenantMembership.id == membership_id).with_for_update())
    if membership is None:
        raise ApiError(404, "membership_not_found", "Membership not found.")
    actor_role = db.get(Role, actor_membership.role_id)
    target_role = db.get(Role, membership.role_id)
    if membership.user_id == principal.user.id:
        _check_membership_policy(db, tenant_id=tenant_id, actor_id=principal.user.id,
                                 actor_role=actor_role.name if actor_role else None,
                                 target_id=membership.id,
                                 target_current_role=target_role.name if target_role else None,
                                 target_new_role=None, operation="deactivate_self")
        raise ApiError(409, "cannot_deactivate_self", "You cannot deactivate your own membership.")
    _check_membership_policy(db, tenant_id=tenant_id, actor_id=principal.user.id,
                             actor_role=actor_role.name if actor_role else None,
                             target_id=membership.id,
                             target_current_role=target_role.name if target_role else None,
                             target_new_role=None, operation="deactivate")
    if membership.is_active and target_role and target_role.name == "owner":
        # Owners are only allowed to change another owner's active state.
        # The tenant row lock serializes this count with all other owner changes.
        other_owner = db.scalar(select(TenantMembership.id).join(
            Role, Role.id == TenantMembership.role_id).where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.is_active.is_(True), Role.name == "owner",
            TenantMembership.id != membership.id).limit(1))
        if other_owner is None:
            raise ApiError(409, "last_owner", "The last active tenant owner cannot be deactivated.")
    membership.is_active = False
    record_audit(db, tenant_id=tenant_id, actor_id=principal.user.id, action="staff.deactivated", resource_type="membership", resource_id=membership.id)
    return OkResponse()


@router.patch("/tenants/{tenant_id}/memberships/{membership_id}/reactivate", response_model=OkResponse)
def reactivate_membership(tenant_id: UUID, membership_id: UUID,
                          principal: Principal = Depends(current_principal), db: Session = Depends(get_db, scope="function")):
    actor_membership = authorize_tenant(db, principal, tenant_id, "staff:manage")
    lock_tenant_memberships(db, tenant_id)
    membership = db.scalar(select(TenantMembership).where(
        TenantMembership.tenant_id == tenant_id,
        TenantMembership.id == membership_id,
    ).with_for_update())
    if membership is None:
        raise ApiError(404, "membership_not_found", "Membership not found.")
    actor_role = db.get(Role, actor_membership.role_id)
    target_role = db.get(Role, membership.role_id)
    if membership.user_id == principal.user.id:
        _check_membership_policy(db, tenant_id=tenant_id, actor_id=principal.user.id,
                                 actor_role=actor_role.name if actor_role else None,
                                 target_id=membership.id,
                                 target_current_role=target_role.name if target_role else None,
                                 target_new_role=target_role.name if target_role else None,
                                 operation="reactivate_self")
        raise ApiError(409, "cannot_change_self_membership", "You cannot reactivate your own membership.")
    _check_membership_policy(db, tenant_id=tenant_id, actor_id=principal.user.id,
                             actor_role=actor_role.name if actor_role else None,
                             target_id=membership.id,
                             target_current_role=target_role.name if target_role else None,
                             target_new_role=target_role.name if target_role else None,
                             operation="reactivate")
    if not membership.is_active:
        membership.is_active = True
        record_audit(db, tenant_id=tenant_id, actor_id=principal.user.id,
                     action="staff.reactivated", resource_type="membership",
                     resource_id=membership.id)
    return OkResponse()


@router.patch("/tenants/{tenant_id}/memberships/{membership_id}/role")
def update_membership_role(tenant_id: UUID, membership_id: UUID, payload: MembershipRolePatch,
                           principal: Principal = Depends(current_principal), db: Session = Depends(get_db, scope="function")):
    actor_membership = authorize_tenant(db, principal, tenant_id, "staff:manage")
    lock_tenant_memberships(db, tenant_id)
    actor_role = db.get(Role, actor_membership.role_id)
    membership = db.scalar(select(TenantMembership).where(
        TenantMembership.tenant_id == tenant_id, TenantMembership.id == membership_id,
    ).with_for_update())
    if membership is None:
        raise ApiError(404, "membership_not_found", "Membership not found.")
    target_role = db.scalar(select(Role).where(Role.tenant_id == tenant_id, Role.name == payload.role))
    if target_role is None:
        raise ApiError(404, "role_not_found", "Role not found.")
    current_role = db.get(Role, membership.role_id)
    if membership.user_id == principal.user.id:
        _check_membership_policy(db, tenant_id=tenant_id, actor_id=principal.user.id,
                                 actor_role=actor_role.name if actor_role else None,
                                 target_id=membership.id,
                                 target_current_role=current_role.name if current_role else None,
                                 target_new_role=target_role.name, operation="change_self_role")
        raise ApiError(409, "cannot_change_self_role", "You cannot change your own membership role.")
    _check_membership_policy(db, tenant_id=tenant_id, actor_id=principal.user.id,
                             actor_role=actor_role.name if actor_role else None,
                             target_id=membership.id,
                             target_current_role=current_role.name if current_role else None,
                             target_new_role=target_role.name, operation="change_role")
    if membership.is_active and current_role and current_role.name == "owner" and target_role.name != "owner":
        other_owner = db.scalar(select(TenantMembership.id).join(
            Role, Role.id == TenantMembership.role_id).where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.is_active.is_(True), Role.name == "owner",
            TenantMembership.id != membership.id).limit(1))
        if other_owner is None:
            raise ApiError(409, "last_owner", "The last active tenant owner cannot be demoted.")
    membership.role_id = target_role.id
    record_audit(db, tenant_id=tenant_id, actor_id=principal.user.id,
                 action="staff.role_changed", resource_type="membership",
                 resource_id=membership.id, details={"role": target_role.name})
    return {"membership_id": str(membership.id), "role": target_role.name}


@router.put("/tenants/{tenant_id}/memberships/{membership_id}/shops")
def assign_membership_shops(tenant_id: UUID, membership_id: UUID, shop_ids: list[UUID], principal: Principal = Depends(current_principal), db: Session = Depends(get_db, scope="function")):
    actor_membership = authorize_tenant(db, principal, tenant_id, "staff:manage")
    lock_tenant_memberships(db, tenant_id)
    membership = db.scalar(select(TenantMembership).where(TenantMembership.tenant_id == tenant_id, TenantMembership.id == membership_id))
    if membership is None:
        raise ApiError(404, "membership_not_found", "Membership not found.")
    actor_role = db.get(Role, actor_membership.role_id)
    target_role = db.get(Role, membership.role_id)
    if membership.user_id == principal.user.id:
        _check_membership_policy(db, tenant_id=tenant_id, actor_id=principal.user.id,
                                 actor_role=actor_role.name if actor_role else None,
                                 target_id=membership.id,
                                 target_current_role=target_role.name if target_role else None,
                                 target_new_role=target_role.name if target_role else None,
                                 operation="assign_shops_self")
        raise ApiError(409, "cannot_change_self_membership", "You cannot change your own membership.")
    _check_membership_policy(db, tenant_id=tenant_id, actor_id=principal.user.id,
                             actor_role=actor_role.name if actor_role else None,
                             target_id=membership.id,
                             target_current_role=target_role.name if target_role else None,
                             target_new_role=target_role.name if target_role else None,
                             operation="assign_shops")
    if not membership.is_active:
        raise ApiError(409, "membership_inactive", "Reactivate the membership before changing shop assignments.")
    if membership.all_shops:
        raise ApiError(409, "membership_all_shops", "This membership has tenant-wide shop access.")
    if len(shop_ids) != len(set(shop_ids)):
        raise ApiError(422, "invalid_shop_assignment", "A shop may only be assigned once.")
    valid = set(db.scalars(select(Shop.id).where(Shop.tenant_id == tenant_id, Shop.id.in_(shop_ids), Shop.is_active.is_(True))).all()) if shop_ids else set()
    if valid != set(shop_ids):
        raise ApiError(422, "invalid_shop_assignment", "One or more shops are not available in this tenant.")
    db.query(ShopAssignment).filter_by(tenant_id=tenant_id, membership_id=membership.id).delete(synchronize_session=False)
    db.add_all([ShopAssignment(tenant_id=tenant_id, membership_id=membership.id, shop_id=shop_id) for shop_id in shop_ids])
    record_audit(db, tenant_id=tenant_id, actor_id=principal.user.id, action="staff.shops_assigned", resource_type="membership", resource_id=membership.id, details={"shop_count": len(shop_ids)})
    return OkResponse()


@router.post("/tenants/{tenant_id}/devices", status_code=201)
@limiter.limit("20/hour")
def register_device(request: Request, tenant_id: UUID, payload: DeviceCreate, principal: Principal = Depends(current_principal), db: Session = Depends(get_db, scope="function")):
    membership = authorize_tenant(db, principal, tenant_id, "device:manage")
    authorize_shop(db, membership, payload.shop_id)
    require_subscription(db, tenant_id)
    active_count = db.scalar(select(func.count(Device.id)).where(Device.tenant_id == tenant_id, Device.shop_id == payload.shop_id, Device.revoked_at.is_(None))) or 0
    plan = current_plan(db, tenant_id)
    if active_count >= plan.max_devices_per_shop:
        raise ApiError(403, "device_limit_reached", "The active plan does not allow another device for this shop.")
    credential = secrets.token_urlsafe(48)
    device = Device(tenant_id=tenant_id, shop_id=payload.shop_id, name=payload.name.strip(), platform=payload.platform, credential_hash=hash_token(credential), registered_by=principal.user.id)
    db.add(device)
    db.flush()
    record_audit(db, tenant_id=tenant_id, actor_id=principal.user.id, action="device.registered", resource_type="device", resource_id=device.id, shop_id=device.shop_id)
    return {"id": str(device.id), "shop_id": str(device.shop_id), "name": device.name, "device_credential": credential}


@router.get("/tenants/{tenant_id}/devices")
def list_devices(tenant_id: UUID, principal: Principal = Depends(current_principal), db: Session = Depends(get_db, scope="function")):
    membership = authorize_tenant(db, principal, tenant_id, "device:manage")
    query = select(Device).where(Device.tenant_id == tenant_id)
    if not membership.all_shops:
        query = query.join(ShopAssignment, ShopAssignment.shop_id == Device.shop_id).where(ShopAssignment.membership_id == membership.id)
    devices = db.scalars(query.order_by(Device.created_at.desc())).all()
    return [{"id": str(d.id), "shop_id": str(d.shop_id), "name": d.name, "platform": d.platform, "registered_at": d.created_at, "revoked_at": d.revoked_at} for d in devices]


@router.delete("/tenants/{tenant_id}/devices/{device_id}", response_model=OkResponse)
def revoke_device(tenant_id: UUID, device_id: UUID, principal: Principal = Depends(current_principal), db: Session = Depends(get_db, scope="function")):
    membership = authorize_tenant(db, principal, tenant_id, "device:manage")
    device = db.scalar(select(Device).where(Device.tenant_id == tenant_id, Device.id == device_id).with_for_update())
    if device is None:
        raise ApiError(404, "device_not_found", "Device not found.")
    authorize_shop(db, membership, device.shop_id, allow_inactive=True)
    if device.revoked_at is None:
        device.revoked_at = datetime.now(timezone.utc)
    record_audit(db, tenant_id=tenant_id, actor_id=principal.user.id, action="device.revoked", resource_type="device", resource_id=device.id, shop_id=device.shop_id)
    return OkResponse()


def _summary(tenant_id: UUID, shop_id: UUID | None, db: Session, accessible_shop_ids: set[UUID] | None = None) -> SummaryResponse:
    shops = select(func.count(Shop.id)).where(Shop.tenant_id == tenant_id)
    users = select(func.count(TenantMembership.id)).where(TenantMembership.tenant_id == tenant_id, TenantMembership.is_active.is_(True))
    sales = select(func.count(Sale.id), func.coalesce(func.sum(Sale.total_amount), 0)).where(Sale.tenant_id == tenant_id)
    stock = select(func.coalesce(func.sum(MedicineBatch.quantity), 0)).where(MedicineBatch.tenant_id == tenant_id)
    if shop_id is not None:
        sales = sales.where(Sale.shop_id == shop_id)
        stock = stock.where(MedicineBatch.shop_id == shop_id)
        shops = shops.where(Shop.id == shop_id)
        users = users.where(
            TenantMembership.id.in_(
                select(ShopAssignment.membership_id).where(ShopAssignment.tenant_id == tenant_id, ShopAssignment.shop_id == shop_id)
            ) | TenantMembership.all_shops.is_(True)
        )
    elif accessible_shop_ids is not None:
        shops = shops.where(Shop.id.in_(accessible_shop_ids))
        sales = sales.where(Sale.shop_id.in_(accessible_shop_ids))
        stock = stock.where(MedicineBatch.shop_id.in_(accessible_shop_ids))
        users = users.where(
            TenantMembership.id.in_(
                select(ShopAssignment.membership_id).where(ShopAssignment.tenant_id == tenant_id, ShopAssignment.shop_id.in_(accessible_shop_ids))
            ) | TenantMembership.all_shops.is_(True)
        )
    sale_count, sales_total = db.execute(sales).one()
    return SummaryResponse(tenant_id=tenant_id, shop_id=shop_id, shop_count=db.scalar(shops) or 0, active_users=db.scalar(users) or 0, sales_count=sale_count or 0, sales_total=sales_total, units_in_stock=db.scalar(stock) or 0)


@router.get("/tenants/{tenant_id}/summary", response_model=SummaryResponse)
def tenant_summary(tenant_id: UUID, principal: Principal = Depends(current_principal), db: Session = Depends(get_db, scope="function")):
    membership = authorize_tenant(db, principal, tenant_id, "tenant:read")
    authorize_tenant(db, principal, tenant_id, "sales:read")
    authorize_tenant(db, principal, tenant_id, "inventory:read")
    if membership.all_shops:
        return _summary(tenant_id, None, db)
    assigned = set(db.scalars(select(ShopAssignment.shop_id).where(ShopAssignment.tenant_id == tenant_id, ShopAssignment.membership_id == membership.id)).all())
    return _summary(tenant_id, None, db, accessible_shop_ids=assigned)


@router.get("/tenants/{tenant_id}/shops/{shop_id}/summary", response_model=SummaryResponse)
def shop_summary(tenant_id: UUID, shop_id: UUID, principal: Principal = Depends(current_principal), db: Session = Depends(get_db, scope="function")):
    membership = authorize_tenant(db, principal, tenant_id, "sales:read")
    authorize_tenant(db, principal, tenant_id, "inventory:read")
    authorize_shop(db, membership, shop_id)
    return _summary(tenant_id, shop_id, db)
