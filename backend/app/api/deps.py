from dataclasses import dataclass
from uuid import UUID

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.core.security import decode_access_token
from app.db.session import get_db, set_tenant_context
from app.models import AuthSession, Permission, PlatformOperator, RolePermission, TenantMembership, User

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    user: User
    session_id: UUID


def current_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db, scope="function"),
) -> Principal:
    if credentials is None:
        raise ApiError(401, "authentication_required", "Authentication is required.")
    try:
        claims = decode_access_token(credentials.credentials)
        if claims.get("typ") != "access":
            raise ValueError("wrong token type")
        user_id = UUID(claims["sub"])
        session_id = UUID(claims["sid"])
    except (jwt.PyJWTError, ValueError, KeyError, TypeError):
        raise ApiError(401, "invalid_token", "The access token is invalid or expired.") from None
    session = db.get(AuthSession, session_id)
    user = db.get(User, user_id)
    if session is None or session.user_id != user_id or session.revoked_at is not None or user is None or not user.is_active:
        raise ApiError(401, "invalid_session", "The session is no longer active.")
    return Principal(user=user, session_id=session_id)


def require_platform_operator(principal: Principal = Depends(current_principal), db: Session = Depends(get_db, scope="function")) -> Principal:
    operator = db.get(PlatformOperator, principal.user.id)
    if operator is None or not operator.is_active:
        raise ApiError(403, "platform_operator_required", "Platform operator access is required.")
    db.execute(text("SELECT set_config('app.platform_operator', 'true', true)"))
    return principal


def optional_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db, scope="function"),
) -> Principal | None:
    if credentials is None:
        return None
    return current_principal(credentials=credentials, db=db)


def authorize_tenant(
    db: Session,
    principal: Principal,
    tenant_id: UUID,
    permission_code: str | None = None,
) -> TenantMembership:
    # RLS uses this value only to constrain rows. The following membership and
    # permission checks are still mandatory authorization decisions.
    set_tenant_context(db, tenant_id)
    membership = db.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.user_id == principal.user.id,
            TenantMembership.is_active.is_(True),
        )
    )
    if membership is None:
        raise ApiError(404, "tenant_not_found", "Tenant not found.")
    from app.models import Tenant

    tenant = db.get(Tenant, tenant_id)
    if tenant is None or not tenant.is_active:
        raise ApiError(404, "tenant_not_found", "Tenant not found.")
    if permission_code:
        allowed = db.scalar(
            select(RolePermission.id)
            .join(Permission, Permission.id == RolePermission.permission_id)
            .where(
                RolePermission.tenant_id == tenant_id,
                RolePermission.role_id == membership.role_id,
                Permission.code == permission_code,
            )
        )
        if allowed is None:
            raise ApiError(403, "permission_denied", "You do not have permission to perform this action.")
    return membership


def authorize_shop(db: Session, membership: TenantMembership, shop_id: UUID, allow_inactive: bool = False) -> None:
    from app.models import Shop, ShopAssignment

    query = select(Shop.id).where(Shop.tenant_id == membership.tenant_id, Shop.id == shop_id)
    if not allow_inactive:
        query = query.where(Shop.is_active.is_(True))
    shop = db.scalar(query)
    if shop is None:
        raise ApiError(404, "shop_not_found", "Shop not found.")
    if membership.all_shops:
        return
    assigned = db.scalar(
        select(ShopAssignment.id).where(
            ShopAssignment.tenant_id == membership.tenant_id,
            ShopAssignment.shop_id == shop_id,
            ShopAssignment.membership_id == membership.id,
        )
    )
    if assigned is None:
        raise ApiError(403, "shop_access_denied", "You do not have access to this shop.")


def require_subscription(db: Session, tenant_id: UUID) -> None:
    from datetime import datetime, timezone

    from app.models import SubscriptionPlan, TenantSubscription

    now = datetime.now(timezone.utc)
    rows = db.scalars(
        select(TenantSubscription).join(SubscriptionPlan, SubscriptionPlan.id == TenantSubscription.plan_id).where(
            TenantSubscription.tenant_id == tenant_id,
            SubscriptionPlan.is_active.is_(True),
            TenantSubscription.status.in_(("trialing", "active", "past_due")),
        )
    ).all()
    if not any(row.starts_at <= now and (row.ends_at >= now or (row.grace_until and row.grace_until >= now)) for row in rows):
        raise ApiError(402, "subscription_inactive", "The tenant subscription is inactive.")


def current_plan(db: Session, tenant_id: UUID):
    from datetime import datetime, timezone

    from sqlalchemy import select

    from app.models import SubscriptionPlan, TenantSubscription

    now = datetime.now(timezone.utc)
    rows = db.execute(
        select(TenantSubscription, SubscriptionPlan)
        .join(SubscriptionPlan, SubscriptionPlan.id == TenantSubscription.plan_id)
        .where(
            TenantSubscription.tenant_id == tenant_id,
            TenantSubscription.status.in_(("trialing", "active", "past_due")),
            TenantSubscription.starts_at <= now,
        )
        .order_by(TenantSubscription.ends_at.desc())
    ).all()
    for subscription, plan in rows:
        if plan.is_active and (subscription.ends_at >= now or (subscription.grace_until and subscription.grace_until >= now)):
            return plan
    raise ApiError(402, "subscription_inactive", "The tenant subscription is inactive.")
