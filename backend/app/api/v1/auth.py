from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import Principal, current_principal, optional_principal
from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.rbac import assert_can_modify_membership, lock_tenant_memberships
from app.core.rate_limit import limiter
from app.core.security import (
    create_access_token, hash_password, hash_token, new_refresh_token, verify_password,
)
from app.db.session import get_db, set_tenant_context, set_user_context
from app.models import (
    AuthSession, Permission, Role, RolePermission, Shop, ShopAssignment, SubscriptionPlan,
    Tenant, TenantMembership, TenantSubscription, User, UserInvitation,
)
from app.schemas import (
    InvitationAccept, LoginRequest, OkResponse, RefreshRequest, RegisterTenant,
    TenantRegistrationResponse, TokenResponse,
)
from app.services.audit import record_audit

router = APIRouter(prefix="/auth", tags=["authentication"])

# Verified when an account does not exist (or is inactive) so every login
# attempt performs the same Argon2 work and response timing does not reveal
# whether an email is registered.
_DUMMY_PASSWORD_HASH = hash_password("solona-login-timing-equalization")

PERMISSIONS = {
    "tenant:read": "View tenant configuration and summaries",
    "tenant:manage": "Update tenant and shop configuration",
    "staff:manage": "Invite, assign, and deactivate tenant staff",
    "device:manage": "Register and revoke shop devices",
    "inventory:read": "View medicine and batch inventory",
    "inventory:write": "Manage medicine catalogue and stock receipts",
    "sales:read": "View shop sales summaries",
    "sales:write": "Record sales and payments",
    "billing:read": "View subscription and payment records",
    "billing:manage": "Initiate subscription payments and manage billing",
}


def issue_tokens(db: Session, user: User, family_id: UUID | None = None) -> TokenResponse:
    settings = get_settings()
    raw_refresh = new_refresh_token()
    session = AuthSession(
        user_id=user.id,
        family_id=family_id or uuid4(),
        refresh_token_hash=hash_token(raw_refresh),
        expires_at=datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_days),
    )
    db.add(session)
    db.flush()
    access, _ = create_access_token(user.id, session.id)
    return TokenResponse(
        access_token=access,
        refresh_token=raw_refresh,
        expires_in=settings.access_token_minutes * 60,
    )


@router.post("/register-tenant", response_model=TenantRegistrationResponse, status_code=201)
@limiter.limit("3/hour")
def register_tenant(request: Request, response: Response, payload: RegisterTenant, db: Session = Depends(get_db, scope="function")):
    if db.scalar(select(User.id).where(User.email == payload.owner_email)):
        raise ApiError(409, "email_in_use", "An account with that email already exists.")
    plan = db.scalar(select(SubscriptionPlan).where(SubscriptionPlan.code == "trial", SubscriptionPlan.is_active.is_(True)))
    if plan is None:
        raise ApiError(503, "registration_unavailable", "Tenant registration is temporarily unavailable.")

    tenant = Tenant(id=uuid4(), name=payload.tenant_name.strip(), slug=payload.tenant_slug)
    user = User(email=payload.owner_email, full_name=payload.owner_name.strip(), password_hash=hash_password(payload.owner_password))
    set_tenant_context(db, tenant.id)
    db.add_all([tenant, user])
    try:
        db.flush()
    except IntegrityError as error:
        db.rollback()
        # RLS hides other tenants from any pre-insert slug lookup, so slug
        # and email uniqueness are enforced by the database; map the violated
        # constraint back to a clear, non-leaky client error.
        detail = str(error.orig)
        if "uq_tenants_slug" in detail:
            raise ApiError(409, "tenant_slug_in_use", "That tenant identifier is already in use.") from error
        if "ix_users_email" in detail:
            raise ApiError(409, "email_in_use", "An account with that email already exists.") from error
        raise
    shop = Shop(tenant_id=tenant.id, name=payload.shop_name.strip(), code=payload.shop_code)
    db.add(shop)
    role_permissions = {
        "owner": set(PERMISSIONS),
        "administrator": set(PERMISSIONS),
        "pharmacist": {"tenant:read", "inventory:read", "inventory:write", "sales:read", "sales:write"},
        "inventory_manager": {"tenant:read", "inventory:read", "inventory:write"},
        "cashier": {"tenant:read", "inventory:read", "sales:read", "sales:write"},
    }
    roles = {}
    for role_name, permission_codes in role_permissions.items():
        role = Role(tenant_id=tenant.id, name=role_name, is_system=True)
        db.add(role)
        db.flush()
        roles[role_name] = role

    permissions: list[Permission] = []
    for code, description in PERMISSIONS.items():
        permission = db.scalar(select(Permission).where(Permission.code == code))
        if permission is None:
            permission = Permission(code=code, description=description)
            db.add(permission)
            db.flush()
        permissions.append(permission)
    permission_ids = {p.code: p.id for p in permissions}
    db.add_all([
        RolePermission(tenant_id=tenant.id, role_id=role.id, permission_id=permission_ids[code])
        for role_name, role in roles.items()
        for code in role_permissions[role_name]
    ])
    membership = TenantMembership(tenant_id=tenant.id, user_id=user.id, role_id=roles["owner"].id, all_shops=True)
    db.add(membership)
    db.flush()
    db.add(TenantSubscription(
        tenant_id=tenant.id,
        plan_id=plan.id,
        status="trialing",
        starts_at=datetime.now(timezone.utc),
        ends_at=datetime.now(timezone.utc) + timedelta(days=plan.trial_days or 14),
    ))
    record_audit(db, tenant_id=tenant.id, actor_id=user.id, action="tenant.registered", resource_type="tenant", resource_id=tenant.id, shop_id=shop.id)
    tokens = issue_tokens(db, user)
    return TenantRegistrationResponse(**tokens.model_dump(), tenant_id=tenant.id, shop_id=shop.id)


@router.post("/login", response_model=TokenResponse)
@limiter.limit("5/minute")
def login(request: Request, response: Response, payload: LoginRequest, db: Session = Depends(get_db, scope="function")):
    user = db.scalar(select(User).where(User.email == payload.email))
    password_hash = user.password_hash if user is not None else _DUMMY_PASSWORD_HASH
    password_ok = verify_password(payload.password, password_hash)
    if user is None or not user.is_active or not password_ok:
        raise ApiError(401, "invalid_credentials", "Email or password is incorrect.")
    return issue_tokens(db, user)


@router.get("/workspaces")
def list_workspaces(principal: Principal = Depends(current_principal), db: Session = Depends(get_db, scope="function")):
    """Discover only the authenticated user's active tenant/shop grants."""
    from app.models import Shop, ShopAssignment

    set_user_context(db, principal.user.id)
    memberships = db.scalars(select(TenantMembership).where(
        TenantMembership.user_id == principal.user.id,
        TenantMembership.is_active.is_(True),
    )).all()
    workspaces = []
    for membership in memberships:
        set_tenant_context(db, membership.tenant_id)
        tenant = db.get(Tenant, membership.tenant_id)
        if tenant is None or not tenant.is_active:
            continue
        role = db.get(Role, membership.role_id)
        permissions = db.scalars(
            select(Permission.code).join(RolePermission, RolePermission.permission_id == Permission.id).where(
                RolePermission.tenant_id == membership.tenant_id,
                RolePermission.role_id == membership.role_id,
            )
        ).all()
        shop_query = select(Shop).where(Shop.tenant_id == membership.tenant_id, Shop.is_active.is_(True))
        if not membership.all_shops:
            shop_query = shop_query.join(ShopAssignment, ShopAssignment.shop_id == Shop.id).where(
                ShopAssignment.membership_id == membership.id
            )
        shops = db.scalars(shop_query.order_by(Shop.name)).all()
        workspaces.append({
            "tenant_id": str(tenant.id), "tenant_name": tenant.name,
            "membership_id": str(membership.id), "role": role.name if role else "",
            "permissions": sorted(permissions),
            "shops": [{"shop_id": str(shop.id), "shop_name": shop.name,
                       "timezone": shop.timezone, "receipt_settings": shop.receipt_settings} for shop in shops],
        })
    return {"user_id": str(principal.user.id), "email": principal.user.email,
            "full_name": principal.user.full_name, "workspaces": workspaces}


@router.post("/refresh", response_model=TokenResponse)
@limiter.limit("10/minute")
def refresh(request: Request, response: Response, payload: RefreshRequest, db: Session = Depends(get_db, scope="function")):
    token_hash = hash_token(payload.refresh_token)
    session = db.scalar(select(AuthSession).where(AuthSession.refresh_token_hash == token_hash).with_for_update())
    now = datetime.now(timezone.utc)
    if session is None:
        raise ApiError(401, "invalid_refresh_token", "The refresh token is invalid or expired.")
    if session.revoked_at is not None:
        # Reuse of a rotated credential revokes the complete token family.
        rows = db.scalars(select(AuthSession).where(AuthSession.family_id == session.family_id).with_for_update()).all()
        for row in rows:
            row.revoked_at = row.revoked_at or now
        db.commit()
        raise ApiError(401, "refresh_token_reused", "The refresh token is no longer valid. Sign in again.")
    if session.expires_at <= now:
        session.revoked_at = now
        db.commit()
        raise ApiError(401, "invalid_refresh_token", "The refresh token is invalid or expired.")
    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        session.revoked_at = now
        db.commit()
        raise ApiError(401, "invalid_session", "The account is no longer active.")
    session.revoked_at = now
    replacement = issue_tokens(db, user, session.family_id)
    new_session = db.scalar(select(AuthSession).where(AuthSession.refresh_token_hash == hash_token(replacement.refresh_token)))
    session.replaced_by_id = new_session.id
    return replacement


@router.post("/logout", response_model=OkResponse)
def logout(principal: Principal = Depends(current_principal), db: Session = Depends(get_db, scope="function")):
    session = db.get(AuthSession, principal.session_id)
    if session and session.revoked_at is None:
        session.revoked_at = datetime.now(timezone.utc)
    return OkResponse()


@router.post("/accept-invitation", response_model=OkResponse)
@limiter.limit("10/hour")
def accept_invitation(request: Request, response: Response, payload: InvitationAccept, principal: Principal | None = Depends(optional_principal), db: Session = Depends(get_db, scope="function")):
    try:
        tenant_id = UUID(payload.token.split(".", 1)[0])
    except (ValueError, IndexError):
        raise ApiError(400, "invalid_invitation", "The invitation is invalid or expired.") from None
    set_tenant_context(db, tenant_id)
    lock_tenant_memberships(db, tenant_id)
    invitation = db.scalar(
        select(UserInvitation).where(UserInvitation.token_hash == hash_token(payload.token)).with_for_update()
    )
    now = datetime.now(timezone.utc)
    if invitation is None or invitation.accepted_at is not None or invitation.expires_at <= now:
        raise ApiError(400, "invalid_invitation", "The invitation is invalid or expired.")
    if invitation.tenant_id != tenant_id:
        raise ApiError(400, "invalid_invitation", "The invitation is invalid or expired.")
    role = db.get(Role, invitation.role_id)
    if role is None or role.tenant_id != invitation.tenant_id:
        raise ApiError(400, "invalid_invitation", "The invitation is invalid or expired.")
    inviter_membership = db.execute(
        select(TenantMembership, Role)
        .join(Role, Role.id == TenantMembership.role_id)
        .where(TenantMembership.tenant_id == tenant_id,
               TenantMembership.user_id == invitation.created_by,
               TenantMembership.is_active.is_(True))
        .with_for_update()
    ).first()
    inviter_role = inviter_membership[1].name if inviter_membership else None
    try:
        assert_can_modify_membership(inviter_role, None, role.name)
    except ApiError as error:
        record_audit(db, tenant_id=tenant_id, actor_id=invitation.created_by,
                     action="staff.permission_denied", resource_type="invitation",
                     resource_id=invitation.id,
                     details={"operation": "accept_invitation", "actor_role": inviter_role,
                              "target_current_role": None, "attempted_role": role.name})
        db.commit()
        raise ApiError(403, "inviter_permission_revoked",
                       "The inviter no longer has permission to grant this role.") from error
    user = db.scalar(select(User).where(User.email == invitation.email))
    if user is not None and user.id == invitation.created_by:
        raise ApiError(409, "cannot_accept_own_invitation", "An inviter cannot accept their own invitation.")
    if user is None:
        if payload.password is None:
            raise ApiError(422, "password_required", "A password is required to create this account.")
        user = User(email=invitation.email, full_name=payload.full_name.strip(), password_hash=hash_password(payload.password))
        db.add(user)
        db.flush()
    elif principal is None or principal.user.id != user.id:
        raise ApiError(401, "existing_account_authentication_required", "Sign in to the invited account before accepting this invitation.")
    membership = TenantMembership(tenant_id=invitation.tenant_id, user_id=user.id, role_id=role.id)
    db.add(membership)
    db.flush()
    for shop_id in invitation.shop_ids:
        db.add(ShopAssignment(tenant_id=invitation.tenant_id, shop_id=UUID(shop_id), membership_id=membership.id))
    invitation.accepted_at = now
    record_audit(db, tenant_id=invitation.tenant_id, actor_id=user.id, action="staff.invitation_accepted", resource_type="user", resource_id=user.id)
    return OkResponse()
