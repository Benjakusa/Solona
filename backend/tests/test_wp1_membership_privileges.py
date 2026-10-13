from tests.test_api_integration import register


def _invited_user(client, tenant_id, headers, email, role):
    invite = client.post(
        f"/api/v1/tenants/{tenant_id}/invitations",
        headers=headers,
        json={"email": email, "role": role, "shop_ids": []},
    )
    assert invite.status_code == 201, invite.text
    accepted = client.post(
        "/api/v1/auth/accept-invitation",
        json={"token": invite.json()["invitation_token"], "full_name": "Test User",
              "password": "a-very-long-password-1"},
    )
    assert accepted.status_code == 200, accepted.text
    token = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "a-very-long-password-1"},
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_wp1_administrator_cannot_invite_owner(client):
    tenant, owner_headers = register(client, "wp1-invite-owner")
    tenant_id = tenant["tenant_id"]
    admin_headers = _invited_user(client, tenant_id, owner_headers, "wp1-admin@example.test", "administrator")

    response = client.post(
        f"/api/v1/tenants/{tenant_id}/invitations",
        headers=admin_headers,
        json={"email": "wp1-evil@example.test", "role": "owner", "shop_ids": []},
    )

    assert response.status_code == 403
    assert client.get(f"/api/v1/tenants/{tenant_id}", headers=owner_headers).status_code == 200


def test_wp1_administrator_cannot_demote_or_deactivate_owner(client):
    tenant, owner_headers = register(client, "wp1-admin-owner")
    tenant_id = tenant["tenant_id"]
    _invited_user(client, tenant_id, owner_headers, "wp1-owner2@example.test", "owner")
    admin_headers = _invited_user(client, tenant_id, owner_headers, "wp1-admin2@example.test", "administrator")
    memberships = client.get(f"/api/v1/tenants/{tenant_id}/memberships", headers=owner_headers).json()
    target = next(row for row in memberships if row["email"] == "wp1-owner2@example.test")

    demote = client.patch(
        f"/api/v1/tenants/{tenant_id}/memberships/{target['id']}/role",
        headers=admin_headers, json={"role": "cashier"},
    )
    deactivate = client.patch(
        f"/api/v1/tenants/{tenant_id}/memberships/{target['id']}", headers=admin_headers,
    )

    assert demote.status_code == 403
    assert deactivate.status_code == 403


def test_wp1_concurrent_owner_demotion_keeps_active_owner(client):
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy import func, select
    from app.db.session import SessionLocal, set_tenant_context
    from app.models import Role, TenantMembership

    tenant, owner_one = register(client, "wp1-owner-race")
    tenant_id = tenant["tenant_id"]
    owner_two = _invited_user(client, tenant_id, owner_one, "wp1-race-owner@example.test", "owner")
    members = client.get(f"/api/v1/tenants/{tenant_id}/memberships", headers=owner_one).json()
    ids = {row["email"]: row["id"] for row in members}
    targets = [ids["owner-wp1-owner-race@example.test"], ids["wp1-race-owner@example.test"]]
    actors = [owner_two, owner_one]

    def demote(args):
        actor, target = args
        return client.patch(
            f"/api/v1/tenants/{tenant_id}/memberships/{target}/role",
            headers=actor, json={"role": "cashier"},
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(demote, zip(actors, targets)))

    assert statuses.count(200) == 1
    with SessionLocal() as db:
        set_tenant_context(db, tenant_id)
        count = db.scalar(select(func.count(TenantMembership.id)).join(
            Role, Role.id == TenantMembership.role_id).where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.is_active.is_(True), Role.name == "owner"))
    assert count == 1
