from uuid import UUID

from sqlalchemy.orm import Session

from app.models import AuditEvent, PlatformAuditEvent


def record_audit(
    db: Session,
    *,
    tenant_id: UUID,
    actor_id: UUID | None,
    action: str,
    resource_type: str,
    resource_id: UUID | None = None,
    shop_id: UUID | None = None,
    transaction_id: UUID | None = None,
    details: dict | None = None,
) -> AuditEvent:
    event = AuditEvent(
        tenant_id=tenant_id,
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        shop_id=shop_id,
        transaction_id=transaction_id,
        details=details or {},
    )
    db.add(event)
    return event


def record_platform_audit(db: Session, *, actor_id: UUID, action: str,
                         resource_type: str, resource_id: UUID | None = None,
                         tenant_id: UUID | None = None, details: dict | None = None) -> PlatformAuditEvent:
    event = PlatformAuditEvent(actor_id=actor_id, tenant_id=tenant_id, action=action,
                               resource_type=resource_type, resource_id=resource_id,
                               details=details or {})
    db.add(event)
    return event
