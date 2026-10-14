"""Start the SOLONA demo API and create local-only owner/client accounts.

Requires PostgreSQL and the backend Python requirements. Credentials printed by
this script are for a private development demo only; do not expose this setup.
"""
from __future__ import annotations

import os
import secrets
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"


def main() -> int:
    if not (BACKEND / ".env").exists():
        print("Missing backend/.env. Copy backend/.env.example and configure a PostgreSQL DATABASE_URL.", file=sys.stderr)
        return 2
    os.chdir(BACKEND)
    sys.path.insert(0, str(BACKEND))
    from app.core.config import get_settings
    from app.core.security import hash_password
    from app.db.session import SessionLocal, set_tenant_context
    from app.models import (  # noqa: PLC0415
        Permission, PlatformOperator, Role, RolePermission, Shop, ShopAssignment,
        SubscriptionPlan, Tenant, TenantMembership, TenantSubscription, User,
    )
    from sqlalchemy import select
    from datetime import datetime, timedelta, timezone

    settings = get_settings()
    if settings.app_env == "production":
        print("Refusing to seed demo accounts in production.", file=sys.stderr)
        return 2

    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)
    owner_email = os.getenv("DEMO_OWNER_EMAIL", "owner@solona.test").strip().lower()
    client_email = os.getenv("DEMO_CLIENT_EMAIL", "client@solona.test").strip().lower()
    owner_password = os.getenv("DEMO_OWNER_PASSWORD") or secrets.token_urlsafe(18)
    client_password = os.getenv("DEMO_CLIENT_PASSWORD") or secrets.token_urlsafe(18)

    with SessionLocal.begin() as db:
        plan = db.scalar(select(SubscriptionPlan).where(SubscriptionPlan.code == "trial"))
        if plan is None:
            raise RuntimeError("Trial plan was not seeded by the migrations")
        permissions = {p.code: p for p in db.scalars(select(Permission)).all()}
        owner = db.scalar(select(User).where(User.email == owner_email))
        if owner is None:
            owner = User(email=owner_email, full_name="SOLONA Demo Owner", password_hash=hash_password(owner_password))
            db.add(owner)
            db.flush()
        elif os.getenv("DEMO_OWNER_PASSWORD"):
            owner.password_hash = hash_password(owner_password)
        client = db.scalar(select(User).where(User.email == client_email))
        if client is None:
            client = User(email=client_email, full_name="Demo Pharmacy Client", password_hash=hash_password(client_password))
            db.add(client)
            db.flush()
        elif os.getenv("DEMO_CLIENT_PASSWORD"):
            client.password_hash = hash_password(client_password)

        if not db.get(PlatformOperator, owner.id):
            db.add(PlatformOperator(user_id=owner.id))

        for slug, name, shop_name, shop_code, user, role_name, grants_all in [
            ("solona-demo", "SOLONA Demo Workspace", "Demo Pharmacy", "DEMO", owner, "owner", True),
            ("client-demo", "Client Demo Workspace", "Client Pharmacy", "CLIENT", client, "owner", True),
        ]:
            # Forced RLS on `tenants` requires the transaction-local tenant
            # context to match the new row id before the insert (same pattern
            # as auth.register_tenant). The deterministic id formula must stay
            # in sync with scripts/seed_demo.py so both scripts share the
            # solona-demo tenant instead of colliding on its slug.
            tenant_id = uuid.uuid5(uuid.NAMESPACE_URL, f"solona.dev.seed/tenant:{slug}")
            set_tenant_context(db, tenant_id)
            tenant = db.scalar(select(Tenant).where(Tenant.slug == slug))
            if tenant is None:
                tenant = Tenant(id=tenant_id, name=name, slug=slug)
                db.add(tenant)
                db.flush()
                shop = Shop(tenant_id=tenant.id, name=shop_name, code=shop_code)
                db.add(shop)
                role = db.scalar(select(Role).where(Role.tenant_id == tenant.id, Role.name == role_name))
                if role is None:
                    role = Role(tenant_id=tenant.id, name=role_name, is_system=True)
                    db.add(role)
                    db.flush()
                for permission in permissions.values():
                    db.add(RolePermission(tenant_id=tenant.id, role_id=role.id, permission_id=permission.id))
                db.add(TenantMembership(tenant_id=tenant.id, user_id=user.id, role_id=role.id, all_shops=grants_all))
                now = datetime.now(timezone.utc)
                db.add(TenantSubscription(tenant_id=tenant.id, plan_id=plan.id, status="trialing",
                                          starts_at=now, ends_at=now + timedelta(days=14)))
    print("Demo data is ready. Keep this service private.")
    print(f"App owner: {owner_email} / {owner_password}")
    print(f"Client:    {client_email} / {client_password}")
    print("API: http://127.0.0.1:8000/api/v1/docs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
