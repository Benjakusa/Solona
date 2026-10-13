"""Tenant membership change policy shared by every membership workflow."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.models import Role, Tenant, TenantMembership

_RANK = {"owner": 3, "administrator": 2}


def role_rank(role_name: str | None) -> int:
    return _RANK.get(role_name or "", 1)


def assert_can_modify_membership(
    actor_role: str | None,
    target_current_role: str | None,
    target_new_role: str | None,
) -> None:
    """Enforce role hierarchy for create, role change, activation and removal."""
    target_is_privileged = any(
        role in {"owner", "administrator"}
        for role in (target_current_role, target_new_role)
        if role is not None
    )
    if target_is_privileged and actor_role != "owner":
        raise ApiError(403, "owner_required", "Only a tenant owner can manage owners or administrators.")
    if actor_role not in {"owner", "administrator"}:
        raise ApiError(403, "permission_denied", "Only an owner or administrator can manage memberships.")


def lock_tenant_memberships(db: Session, tenant_id) -> None:
    """Serialize role/active-state changes against the tenant row."""
    if db.scalar(select(Tenant.id).where(Tenant.id == tenant_id).with_for_update()) is None:
        raise ApiError(404, "tenant_not_found", "Tenant not found.")


def assert_owner_remains(db: Session, tenant_id) -> None:
    """Require an active owner while holding the tenant membership lock."""
    owners = db.scalar(
        select(TenantMembership.id)
        .join(Role, Role.id == TenantMembership.role_id)
        .where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.is_active.is_(True),
            Role.name == "owner",
        )
        .limit(1)
    )
    if owners is None:
        raise ApiError(409, "last_owner", "A tenant must always have at least one active owner.")
