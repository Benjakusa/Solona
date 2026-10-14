"""Authentication, account-creation, and RBAC integration tests.

Covers admin login, user-account creation (tenant registration and
invitations), token lifecycle, the role/permission matrix, role hierarchy,
tenant/shop isolation, and the platform-operator gate. Positive and negative
scenarios are both exercised through the public API.
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.core.security import hash_token
from app.db.session import SessionLocal, set_tenant_context
from app.models import (
    AuditEvent, PlatformOperator, Role, TenantMembership, TenantSubscription, User,
    UserInvitation,
)
from sqlalchemy import select

ALL_PERMISSIONS = {
    "tenant:read", "tenant:manage", "staff:manage", "device:manage",
    "inventory:read", "inventory:write", "sales:read", "sales:write",
    "billing:read", "billing:manage",
}
ROLE_PERMISSIONS = {
    "owner": ALL_PERMISSIONS,
    "administrator": ALL_PERMISSIONS,
    "pharmacist": {"tenant:read", "inventory:read", "inventory:write", "sales:read", "sales:write"},
    "inventory_manager": {"tenant:read", "inventory:read", "inventory:write"},
    "cashier": {"tenant:read", "inventory:read", "sales:read", "sales:write"},
}

_counter = iter(range(1000, 100000))


def _suffix() -> str:
    return f"t{next(_counter)}"


def register(client, suffix: str | None = None, **overrides):
    """Register a tenant with an owner account; returns (response, payload)."""
    suffix = suffix or _suffix()
    payload = {
        "tenant_name": f"Pharmacy {suffix}",
        "tenant_slug": f"pharmacy-{suffix}",
        "shop_name": "Main Shop",
        "shop_code": "MAIN",
        "owner_email": f"owner-{suffix}@example.test",
        "owner_name": "Test Owner",
        "owner_password": "a-strong-test-password",
    }
    payload.update(overrides)
    response = client.post("/api/v1/auth/register-tenant", json=payload)
    return response, payload


def auth_headers(result) -> dict:
    return {"Authorization": f"Bearer {result['access_token']}"}


def login(client, email: str, password: str = "a-strong-test-password"):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def invite(client, tenant_id, headers, email: str, role: str, shop_ids=()):
    return client.post(f"/api/v1/tenants/{tenant_id}/invitations", headers=headers,
                       json={"email": email, "role": role, "shop_ids": [str(s) for s in shop_ids]})


def invite_and_accept(client, tenant_id, headers, email: str, role: str, shop_ids=()):
    """Owner/admin invites ``email`` and the invitee accepts, creating the account."""
    invited = invite(client, tenant_id, headers, email, role, shop_ids)
    assert invited.status_code == 201, invited.text
    accepted = client.post("/api/v1/auth/accept-invitation", json={
        "token": invited.json()["invitation_token"],
        "full_name": "Invited Member",
        "password": "another-strong-password",
    })
    assert accepted.status_code == 200, accepted.text
    member = login(client, email, "another-strong-password")
    assert member.status_code == 200, member.text
    return auth_headers(member.json())


def create_second_shop(client, tenant_id, headers, code="WEST"):
    response = client.post(f"/api/v1/tenants/{tenant_id}/shops", headers=headers,
                           json={"name": "West Shop", "code": code})
    assert response.status_code == 201, response.text
    return response.json()


def create_inventory(client, tenant_id, headers, shop_id, sku="SKU-001", quantity=20):
    """Create a medicine and receive a purchasable batch in the shop."""
    medicine = client.post(f"/api/v1/tenants/{tenant_id}/medicines", headers=headers,
                           json={"sku": sku, "name": f"Medicine {sku}", "base_unit": "tablet"})
    assert medicine.status_code == 201, medicine.text
    supplier = client.post(f"/api/v1/tenants/{tenant_id}/suppliers", headers=headers,
                           json={"name": "Local Supplier"})
    assert supplier.status_code == 201, supplier.text
    purchase = client.post(f"/api/v1/tenants/{tenant_id}/purchases", headers=headers, json={
        "shop_id": str(shop_id),
        "supplier_id": supplier.json()["id"],
        "transaction_id": str(uuid4()),
        "items": [{"medicine_id": medicine.json()["id"], "batch_no": f"B-{sku}",
                   "expiry_date": "2030-10-31", "quantity": quantity,
                   "purchase_cost": "2.50", "selling_price": "5.00"}],
    })
    assert purchase.status_code == 201, purchase.text
    batches = client.get(f"/api/v1/tenants/{tenant_id}/shops/{shop_id}/batches", headers=headers)
    assert batches.status_code == 200, batches.text
    batch = next(b for b in batches.json() if b["sku"] == sku)
    return medicine.json(), batch


def sale_payload(shop_id, batch_id, quantity=1, invoice=None, amount_paid=None):
    return {"shop_id": str(shop_id), "transaction_id": str(uuid4()),
            "invoice_no": invoice or f"INV-{uuid4().hex[:10]}",
            "items": [{"batch_id": str(batch_id), "quantity": quantity}],
            "payment_method": "cash",
            "amount_paid": amount_paid or f"{5 * quantity}.00"}


def workspace_for(client, headers) -> dict:
    response = client.get("/api/v1/auth/workspaces", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Authentication: admin (owner) login and token lifecycle
# ---------------------------------------------------------------------------

def test_owner_admin_login_returns_tokens_and_full_workspace(client):
    result, payload = register(client)
    assert result.status_code == 201, result.text
    body = result.json()
    assert {"access_token", "refresh_token", "expires_in", "tenant_id", "shop_id"} <= set(body)

    signed_in = login(client, payload["owner_email"], payload["owner_password"])
    assert signed_in.status_code == 200, signed_in.text
    tokens = signed_in.json()
    assert tokens["token_type"] == "bearer" and tokens["expires_in"] > 0

    discovery = workspace_for(client, auth_headers(tokens))
    assert discovery["email"] == payload["owner_email"]
    assert len(discovery["workspaces"]) == 1
    workspace = discovery["workspaces"][0]
    assert workspace["role"] == "owner"
    assert set(workspace["permissions"]) == ALL_PERMISSIONS
    assert [shop["shop_name"] for shop in workspace["shops"]] == ["Main Shop"]


def test_login_rejects_wrong_password_and_unknown_email_identically(client):
    result, payload = register(client)
    assert result.status_code == 201
    wrong_password = login(client, payload["owner_email"], "not-the-password")
    unknown_email = login(client, "nobody-here@example.test", "a-strong-test-password")
    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json()["error"]["code"] == "invalid_credentials"
    assert unknown_email.json()["error"]["code"] == "invalid_credentials"
    # The same message for both cases avoids account enumeration.
    assert wrong_password.json()["error"]["message"] == unknown_email.json()["error"]["message"]


def test_login_rejects_inactive_account(client):
    result, payload = register(client)
    assert result.status_code == 201
    with SessionLocal.begin() as db:
        user = db.scalar(select(User).where(User.email == payload["owner_email"]))
        user.is_active = False
    assert login(client, payload["owner_email"], payload["owner_password"]).status_code == 401


def test_login_validation_rejects_bad_email_and_empty_password(client):
    register(client)
    bad_email = client.post("/api/v1/auth/login", json={"email": "not-an-email", "password": "x"})
    empty_password = client.post("/api/v1/auth/login", json={"email": "someone@example.test", "password": ""})
    assert bad_email.status_code == empty_password.status_code == 422
    assert bad_email.json()["error"]["code"] == "validation_error"


def test_missing_and_malformed_access_tokens_are_rejected(client):
    result, _ = register(client)
    tokens = result.json()
    assert client.get("/api/v1/auth/workspaces").status_code == 401
    assert client.get("/api/v1/auth/workspaces",
                      headers={"Authorization": "Bearer not-a-jwt"}).status_code == 401
    # A refresh credential is never accepted as an access token.
    response = client.get("/api/v1/auth/workspaces",
                          headers={"Authorization": f"Bearer {tokens['refresh_token']}"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


def test_tampered_access_token_is_rejected(client):
    result, _ = register(client)
    token = result.json()["access_token"]
    tampered = token[:-2] + ("AA" if token[-2:] != "AA" else "BB")
    response = client.get("/api/v1/auth/workspaces", headers={"Authorization": f"Bearer {tampered}"})
    assert response.status_code == 401


def test_refresh_rotates_tokens_and_old_credential_stops_working(client):
    result, _ = register(client)
    tokens = result.json()
    refreshed = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["refresh_token"] != tokens["refresh_token"]
    assert client.get("/api/v1/auth/workspaces",
                      headers=auth_headers(refreshed.json())).status_code == 200
    reused = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert reused.status_code == 401
    assert reused.json()["error"]["code"] == "refresh_token_reused"


def test_refresh_reuse_revokes_the_whole_token_family(client):
    result, _ = register(client)
    tokens = result.json()
    rotated = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).json()
    # Reusing the retired credential revokes every descendant session.
    client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    family_blocked = client.post("/api/v1/auth/refresh", json={"refresh_token": rotated["refresh_token"]})
    assert family_blocked.status_code == 401
    assert client.get("/api/v1/auth/workspaces", headers=auth_headers(rotated)).status_code == 401


def test_unknown_refresh_token_is_rejected(client):
    register(client)
    response = client.post("/api/v1/auth/refresh", json={"refresh_token": "u" * 64})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_refresh_token"


def test_logout_revokes_session_access_and_refresh(client):
    result, _ = register(client)
    headers = auth_headers(result.json())
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 200
    assert client.get("/api/v1/auth/workspaces", headers=headers).status_code == 401
    refresh_after_logout = client.post("/api/v1/auth/refresh",
                                       json={"refresh_token": result.json()["refresh_token"]})
    assert refresh_after_logout.status_code == 401


def test_deactivated_user_loses_active_session(client):
    result, payload = register(client)
    headers = auth_headers(result.json())
    assert client.get("/api/v1/auth/workspaces", headers=headers).status_code == 200
    with SessionLocal.begin() as db:
        user = db.scalar(select(User).where(User.email == payload["owner_email"]))
        user.is_active = False
    response = client.get("/api/v1/auth/workspaces", headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_session"


def test_validation_errors_do_not_echo_passwords(client):
    weak = client.post("/api/v1/auth/register-tenant", json={
        "tenant_name": "Weak PW Pharmacy", "tenant_slug": f"weak-{_suffix()}",
        "shop_name": "Main Shop", "shop_code": "MAIN",
        "owner_email": "weak-pw@example.test", "owner_name": "Weak",
        "owner_password": "TinyPw1!",
    })
    assert weak.status_code == 422
    assert "TinyPw1!" not in weak.text
    # Validation details carry location/type only, never the submitted value.
    details = weak.json()["error"]["details"]
    assert details and all("input" not in detail for detail in details)
    register(client)
    failed = login(client, "weak-pw@example.test", "attempted-password-value")
    assert failed.status_code == 401
    assert "attempted-password-value" not in failed.text


# ---------------------------------------------------------------------------
# Account creation: tenant registration
# ---------------------------------------------------------------------------

def test_register_tenant_provisions_owner_roles_permissions_and_subscription(client):
    result, payload = register(client)
    assert result.status_code == 201, result.text
    body = result.json()
    tenant_id, shop_id = body["tenant_id"], body["shop_id"]

    with SessionLocal() as db:
        set_tenant_context(db, tenant_id)
        roles = {role.name for role in db.scalars(select(Role).where(Role.tenant_id == tenant_id))}
        assert roles == {"owner", "administrator", "pharmacist", "inventory_manager", "cashier"}
        owner_role = db.scalar(select(Role).where(Role.tenant_id == tenant_id, Role.name == "owner"))
        membership = db.scalar(select(TenantMembership).where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.role_id == owner_role.id,
            TenantMembership.is_active.is_(True)))
        assert membership is not None and membership.all_shops is True
        subscription = db.scalar(select(TenantSubscription).where(TenantSubscription.tenant_id == tenant_id))
        assert subscription is not None and subscription.status == "trialing"
        assert subscription.ends_at > datetime.now(timezone.utc)
        audited = db.scalar(select(AuditEvent.id).where(
            AuditEvent.tenant_id == tenant_id, AuditEvent.action == "tenant.registered"))
        assert audited is not None
        owner_user = db.scalar(select(User).where(User.email == payload["owner_email"]))
    assert shop_id and membership.user_id == owner_user.id


def test_register_tenant_rejects_duplicate_email_and_slug(client):
    result, payload = register(client)
    assert result.status_code == 201
    same_email = register(client, owner_email=payload["owner_email"], tenant_slug=f"other-{_suffix()}")
    assert same_email[0].status_code == 409
    assert same_email[0].json()["error"]["code"] == "email_in_use"
    same_slug = register(client, owner_email=f"second-{_suffix()}@example.test",
                         tenant_slug=payload["tenant_slug"])
    assert same_slug[0].status_code == 409
    assert same_slug[0].json()["error"]["code"] == "tenant_slug_in_use"


def test_register_tenant_rejects_invalid_field_values(client):
    suffix = _suffix()
    weak_password = register(client, owner_password="TooShort1")
    assert weak_password[0].status_code == 422
    bad_email = register(client, owner_email="not-an-email")
    assert bad_email[0].status_code == 422
    bad_slug = register(client, tenant_slug="Bad Slug!")
    assert bad_slug[0].status_code == 422
    bad_shop_code = register(client, shop_code="MAIN SHOP")
    assert bad_shop_code[0].status_code == 422
    missing_field = client.post("/api/v1/auth/register-tenant", json={"tenant_name": f"P {suffix}"})
    assert missing_field.status_code == 422
    assert missing_field.json()["error"]["code"] == "validation_error"


def test_newly_created_owner_can_use_administrative_endpoints(client):
    result, payload = register(client)
    assert result.status_code == 201
    headers = auth_headers(result.json())
    tenant_id = result.json()["tenant_id"]
    members = client.get(f"/api/v1/tenants/{tenant_id}/memberships", headers=headers)
    assert members.status_code == 200
    assert [row["email"] for row in members.json()] == [payload["owner_email"]]
    roles = client.get(f"/api/v1/tenants/{tenant_id}/roles", headers=headers)
    assert roles.status_code == 200
    permissions_by_role = {row["name"]: set(row["permissions"]) for row in roles.json()}
    assert permissions_by_role == ROLE_PERMISSIONS


# ---------------------------------------------------------------------------
# Account creation: invitations
# ---------------------------------------------------------------------------

def test_invitation_creates_account_with_role_permissions_and_shop_scope(client):
    result, _ = register(client)
    headers = auth_headers(result.json())
    tenant_id, shop_id = result.json()["tenant_id"], result.json()["shop_id"]

    invited = invite(client, tenant_id, headers, "cashier-one@example.test", "cashier", [shop_id])
    assert invited.status_code == 201, invited.text
    invitation = invited.json()
    assert invitation["email"] == "cashier-one@example.test"
    assert invitation["expires_at"]

    accepted = client.post("/api/v1/auth/accept-invitation", json={
        "token": invitation["invitation_token"], "full_name": "Cynthia Cashier",
        "password": "cashier-strong-pass"})
    assert accepted.status_code == 200, accepted.text

    member = login(client, "cashier-one@example.test", "cashier-strong-pass")
    assert member.status_code == 200, member.text
    discovery = workspace_for(client, auth_headers(member.json()))
    assert discovery["full_name"] == "Cynthia Cashier"
    workspace = discovery["workspaces"][0]
    assert workspace["role"] == "cashier"
    assert set(workspace["permissions"]) == ROLE_PERMISSIONS["cashier"]
    assert [shop["shop_id"] for shop in workspace["shops"]] == [str(shop_id)]


def test_invitation_requires_password_when_creating_a_new_account(client):
    result, _ = register(client)
    headers = auth_headers(result.json())
    tenant_id, shop_id = result.json()["tenant_id"], result.json()["shop_id"]
    invited = invite(client, tenant_id, headers, "no-password@example.test", "cashier", [shop_id]).json()
    response = client.post("/api/v1/auth/accept-invitation",
                           json={"token": invited["invitation_token"], "full_name": "No Password"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "password_required"
    assert client.post("/api/v1/auth/login",
                       json={"email": "no-password@example.test", "password": "x"}).status_code == 401


def test_invitations_are_single_use_and_reject_garbage_or_expired_tokens(client):
    result, _ = register(client)
    headers = auth_headers(result.json())
    tenant_id, shop_id = result.json()["tenant_id"], result.json()["shop_id"]

    garbage = client.post("/api/v1/auth/accept-invitation",
                          json={"token": "x" * 60, "full_name": "Garbage", "password": "a-strong-enough-pass"})
    assert garbage.status_code == 400
    assert garbage.json()["error"]["code"] == "invalid_invitation"

    invited = invite(client, tenant_id, headers, "once@example.test", "cashier", [shop_id]).json()
    with SessionLocal.begin() as db:
        set_tenant_context(db, tenant_id)
        row = db.scalar(select(UserInvitation).where(UserInvitation.tenant_id == tenant_id))
        row.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    expired = client.post("/api/v1/auth/accept-invitation",
                          json={"token": invited["invitation_token"], "full_name": "Late",
                                "password": "a-strong-enough-pass"})
    assert expired.status_code == 400
    assert expired.json()["error"]["code"] == "invalid_invitation"

    invited_again = invite(client, tenant_id, headers, "twice@example.test", "cashier", [shop_id]).json()
    accept_payload = {"token": invited_again["invitation_token"], "full_name": "Twice",
                      "password": "a-strong-enough-pass"}
    assert client.post("/api/v1/auth/accept-invitation", json=accept_payload).status_code == 200
    reuse = client.post("/api/v1/auth/accept-invitation", json=accept_payload)
    assert reuse.status_code == 400
    assert reuse.json()["error"]["code"] == "invalid_invitation"


def test_existing_account_must_authenticate_before_accepting_invitation(client):
    result, _ = register(client)
    owner_headers = auth_headers(result.json())
    tenant_id, shop_id = result.json()["tenant_id"], result.json()["shop_id"]

    other, other_payload = register(client)
    assert other.status_code == 201
    other_headers = auth_headers(other.json())
    third_party, third_payload = register(client)
    assert third_party.status_code == 201
    third_party_headers = auth_headers(third_party.json())

    invited = invite(client, tenant_id, owner_headers,
                     other_payload["owner_email"], "cashier", [shop_id]).json()
    token = invited["invitation_token"]
    anonymous = client.post("/api/v1/auth/accept-invitation",
                            json={"token": token, "full_name": "Existing"})
    assert anonymous.status_code == 401
    assert anonymous.json()["error"]["code"] == "existing_account_authentication_required"
    wrong_user = client.post("/api/v1/auth/accept-invitation", headers=third_party_headers,
                             json={"token": token, "full_name": "Existing"})
    assert wrong_user.status_code == 401
    assert wrong_user.json()["error"]["code"] == "existing_account_authentication_required"
    accepted = client.post("/api/v1/auth/accept-invitation", headers=other_headers,
                           json={"token": token, "full_name": "Existing Owner"})
    assert accepted.status_code == 200, accepted.text
    discovery = workspace_for(client, other_headers)
    assert {ws["role"] for ws in discovery["workspaces"]} == {"owner", "cashier"}
    assert third_payload["owner_email"] != other_payload["owner_email"]


def test_cannot_invite_self_or_accept_own_invitation(client):
    result, payload = register(client)
    headers = auth_headers(result.json())
    tenant_id, shop_id = result.json()["tenant_id"], result.json()["shop_id"]
    self_invite = invite(client, tenant_id, headers, payload["owner_email"], "cashier", [shop_id])
    assert self_invite.status_code == 409
    assert self_invite.json()["error"]["code"] == "cannot_invite_self"

    # Defensive guard: even a pre-existing invitation addressed to the inviter
    # cannot be accepted by that inviter.
    raw_token = f"{tenant_id}.{'a' * 64}"
    with SessionLocal.begin() as db:
        set_tenant_context(db, tenant_id)
        owner_role = db.scalar(select(Role).where(Role.tenant_id == tenant_id, Role.name == "cashier"))
        owner_user = db.scalar(select(User).where(User.email == payload["owner_email"]))
        db.add(UserInvitation(
            tenant_id=tenant_id, email=payload["owner_email"], role_id=owner_role.id,
            shop_ids=[str(shop_id)], token_hash=hash_token(raw_token),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
            created_by=owner_user.id))
    accepted = client.post("/api/v1/auth/accept-invitation", headers=headers, json={
        "token": raw_token, "full_name": "Owner", "password": "a-strong-enough-pass"})
    assert accepted.status_code == 409
    assert accepted.json()["error"]["code"] == "cannot_accept_own_invitation"


def test_demoted_inviter_can_no_longer_grant_the_invited_role(client):
    result, _ = register(client)
    owner_headers = auth_headers(result.json())
    tenant_id, shop_id = result.json()["tenant_id"], result.json()["shop_id"]

    admin_headers = invite_and_accept(client, tenant_id, owner_headers,
                                      "admin-two@example.test", "administrator", [shop_id])
    invited = invite(client, tenant_id, admin_headers, "pharmacist-new@example.test",
                     "pharmacist", [shop_id])
    assert invited.status_code == 201, invited.text

    memberships = client.get(f"/api/v1/tenants/{tenant_id}/memberships", headers=owner_headers).json()
    admin_membership = next(row for row in memberships if row["email"] == "admin-two@example.test")
    demoted = client.patch(
        f"/api/v1/tenants/{tenant_id}/memberships/{admin_membership['id']}/role",
        headers=owner_headers, json={"role": "cashier"})
    assert demoted.status_code == 200, demoted.text

    accepted = client.post("/api/v1/auth/accept-invitation", json={
        "token": invited.json()["invitation_token"], "full_name": "Pharmacist",
        "password": "a-strong-enough-pass"})
    assert accepted.status_code == 403
    assert accepted.json()["error"]["code"] == "inviter_permission_revoked"


# ---------------------------------------------------------------------------
# RBAC: role permission matrix enforced through API actions
# ---------------------------------------------------------------------------

def test_pharmacist_can_manage_inventory_and_sales(client):
    result, _ = register(client)
    owner_headers = auth_headers(result.json())
    tenant_id, shop_id = result.json()["tenant_id"], result.json()["shop_id"]
    headers = invite_and_accept(client, tenant_id, owner_headers,
                                "pharmacist-one@example.test", "pharmacist", [shop_id])

    medicine, batch = create_inventory(client, tenant_id, headers, shop_id, sku="PHA-1", quantity=30)
    sale = client.post(f"/api/v1/tenants/{tenant_id}/sales", headers=headers,
                       json=sale_payload(shop_id, batch["id"], quantity=2))
    assert sale.status_code == 201, sale.text
    assert client.get(f"/api/v1/tenants/{tenant_id}/summary", headers=headers).status_code == 200


def test_inventory_manager_cannot_record_sales(client):
    result, _ = register(client)
    owner_headers = auth_headers(result.json())
    tenant_id, shop_id = result.json()["tenant_id"], result.json()["shop_id"]
    _, batch = create_inventory(client, tenant_id, owner_headers, shop_id, sku="INV-1")
    headers = invite_and_accept(client, tenant_id, owner_headers,
                                "stock-keeper@example.test", "inventory_manager", [shop_id])

    created = client.post(f"/api/v1/tenants/{tenant_id}/medicines", headers=headers,
                          json={"sku": "INV-2", "name": "Ibuprofen", "base_unit": "tablet"})
    assert created.status_code == 201, created.text
    denied = client.post(f"/api/v1/tenants/{tenant_id}/sales", headers=headers,
                         json=sale_payload(shop_id, batch["id"]))
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "permission_denied"


def test_cashier_can_sell_but_not_write_inventory_or_administer(client):
    result, _ = register(client)
    owner_headers = auth_headers(result.json())
    tenant_id, shop_id = result.json()["tenant_id"], result.json()["shop_id"]
    _, batch = create_inventory(client, tenant_id, owner_headers, shop_id, sku="CSH-1")
    headers = invite_and_accept(client, tenant_id, owner_headers,
                                "cashier-one@example.test", "cashier", [shop_id])

    sale = client.post(f"/api/v1/tenants/{tenant_id}/sales", headers=headers,
                       json=sale_payload(shop_id, batch["id"]))
    assert sale.status_code == 201, sale.text

    denied_medicine = client.post(f"/api/v1/tenants/{tenant_id}/medicines", headers=headers,
                                  json={"sku": "CSH-2", "name": "Denied", "base_unit": "tablet"})
    assert denied_medicine.status_code == 403
    denied_invite = invite(client, tenant_id, headers, "sneaky@example.test", "cashier", [shop_id])
    assert denied_invite.status_code == 403
    assert denied_invite.json()["error"]["code"] == "permission_denied"
    assert client.get(f"/api/v1/tenants/{tenant_id}/memberships", headers=headers).status_code == 403
    assert client.get(f"/api/v1/tenants/{tenant_id}/roles", headers=headers).status_code == 403
    denied_shop = client.post(f"/api/v1/tenants/{tenant_id}/shops", headers=headers,
                              json={"name": "Rogue Shop", "code": "ROGUE"})
    assert denied_shop.status_code == 403
    denied_device = client.post(f"/api/v1/tenants/{tenant_id}/devices", headers=headers,
                                json={"shop_id": str(shop_id), "name": "Rogue device", "platform": "web"})
    assert denied_device.status_code == 403
    denied_tenant_patch = client.patch(f"/api/v1/tenants/{tenant_id}", headers=headers,
                                       json={"name": "Renamed by cashier"})
    assert denied_tenant_patch.status_code == 403


def test_administrator_can_administer_but_not_grant_privileged_roles(client):
    result, _ = register(client)
    owner_headers = auth_headers(result.json())
    tenant_id, shop_id = result.json()["tenant_id"], result.json()["shop_id"]
    admin_headers = invite_and_accept(client, tenant_id, owner_headers,
                                      "admin-one@example.test", "administrator", [shop_id])

    assert client.get(f"/api/v1/tenants/{tenant_id}/memberships", headers=admin_headers).status_code == 200
    second_shop = create_second_shop(client, tenant_id, admin_headers, code="EAST")
    device = client.post(f"/api/v1/tenants/{tenant_id}/devices", headers=admin_headers,
                         json={"shop_id": str(shop_id), "name": "Till laptop", "platform": "web"})
    assert device.status_code == 201, device.text
    assert "device_credential" in device.json()

    invited_cashier = invite(client, tenant_id, admin_headers, "admin-cashier@example.test",
                             "cashier", [shop_id])
    assert invited_cashier.status_code == 201, invited_cashier.text
    invited_admin = invite(client, tenant_id, admin_headers, "admin-rival@example.test",
                           "administrator", [shop_id])
    assert invited_admin.status_code == 403
    assert invited_admin.json()["error"]["code"] == "owner_required"
    invited_owner = invite(client, tenant_id, admin_headers, "admin-boss@example.test",
                           "owner", [shop_id])
    assert invited_owner.status_code == 403

    # The cashier invitee accepts so a real membership exists to attempt a promotion on.
    accepted = client.post("/api/v1/auth/accept-invitation", json={
        "token": invited_cashier.json()["invitation_token"], "full_name": "Admins Cashier",
        "password": "cashier-strong-pass"})
    assert accepted.status_code == 200, accepted.text
    memberships = client.get(f"/api/v1/tenants/{tenant_id}/memberships", headers=owner_headers).json()
    owner_membership = next(row for row in memberships if row["role"] == "owner")
    cashier_membership = next(row for row in memberships if row["email"] == "admin-cashier@example.test")
    promote = client.patch(
        f"/api/v1/tenants/{tenant_id}/memberships/{cashier_membership['id']}/role",
        headers=admin_headers, json={"role": "administrator"})
    assert promote.status_code == 403
    demote_owner = client.patch(
        f"/api/v1/tenants/{tenant_id}/memberships/{owner_membership['id']}/role",
        headers=admin_headers, json={"role": "cashier"})
    assert demote_owner.status_code == 403
    assert second_shop["code"] == "EAST"


def test_owner_manages_administrators_and_last_owner_is_protected(client):
    result, _ = register(client)
    owner_headers = auth_headers(result.json())
    tenant_id, shop_id = result.json()["tenant_id"], result.json()["shop_id"]

    second_owner_headers = invite_and_accept(client, tenant_id, owner_headers,
                                             "owner-two@example.test", "owner", [shop_id])
    memberships = client.get(f"/api/v1/tenants/{tenant_id}/memberships", headers=owner_headers).json()
    first_owner = next(row for row in memberships if row["role"] == "owner"
                       and row["email"] != "owner-two@example.test")

    # Self-demotion and self-deactivation are always rejected.
    own_role = client.patch(
        f"/api/v1/tenants/{tenant_id}/memberships/{first_owner['id']}/role",
        headers=owner_headers, json={"role": "cashier"})
    assert own_role.status_code == 409
    assert own_role.json()["error"]["code"] == "cannot_change_self_role"
    own_active = client.patch(
        f"/api/v1/tenants/{tenant_id}/memberships/{first_owner['id']}",
        headers=owner_headers, json={"is_active": False})
    assert own_active.status_code == 409
    assert own_active.json()["error"]["code"] == "cannot_deactivate_self"

    # With two owners, one owner may deactivate the other.
    deactivate = client.patch(
        f"/api/v1/tenants/{tenant_id}/memberships/{first_owner['id']}",
        headers=second_owner_headers, json={"is_active": False})
    assert deactivate.status_code == 200, deactivate.text
    assert workspace_for(client, owner_headers)["workspaces"] == []

    reactivated = client.patch(
        f"/api/v1/tenants/{tenant_id}/memberships/{first_owner['id']}/reactivate",
        headers=second_owner_headers)
    assert reactivated.status_code == 200, reactivated.text

    # A remaining owner may demote the other owner, but once demoted the
    # ex-owner has no privilege over the final owner, and the final owner can
    # never remove themselves.
    demote = client.patch(
        f"/api/v1/tenants/{tenant_id}/memberships/{first_owner['id']}/role",
        headers=second_owner_headers, json={"role": "cashier"})
    assert demote.status_code == 200, demote.text
    remaining = client.get(f"/api/v1/tenants/{tenant_id}/memberships",
                           headers=second_owner_headers).json()
    final_owner = next(row for row in remaining if row["role"] == "owner" and row["is_active"])
    assert final_owner["email"] == "owner-two@example.test"
    demoted_against_owner = client.patch(
        f"/api/v1/tenants/{tenant_id}/memberships/{final_owner['id']}/role",
        headers=owner_headers, json={"role": "cashier"})
    assert demoted_against_owner.status_code == 403
    assert demoted_against_owner.json()["error"]["code"] == "owner_required"
    self_demote = client.patch(
        f"/api/v1/tenants/{tenant_id}/memberships/{final_owner['id']}/role",
        headers=second_owner_headers, json={"role": "cashier"})
    assert self_demote.status_code == 409
    assert self_demote.json()["error"]["code"] == "cannot_change_self_role"


def test_membership_policy_backstops_reject_privilege_escalation_and_owner_loss():
    """Direct checks of the shared RBAC policy used by every membership flow."""
    import pytest

    from app.core.errors import ApiError
    from app.core.rbac import assert_can_modify_membership, assert_owner_remains, role_rank

    # Role hierarchy: only owners may manage owners or administrators.
    assert_can_modify_membership("owner", None, "administrator")
    assert_can_modify_membership("administrator", "cashier", "pharmacist")
    for actor, current, new in [
        ("administrator", None, "administrator"),
        ("administrator", "administrator", "cashier"),
        ("administrator", None, "owner"),
        ("cashier", None, "cashier"),
        (None, None, "cashier"),
    ]:
        with pytest.raises(ApiError) as error:
            assert_can_modify_membership(actor, current, new)
        assert error.value.status_code == 403

    assert role_rank("owner") > role_rank("administrator") > role_rank("cashier")


def test_owner_remains_backstop_rejects_tenant_without_active_owner():
    """The tenant row lock path refuses changes that would leave no owner."""
    import pytest

    from app.core.errors import ApiError
    from app.core.rbac import assert_owner_remains
    from app.models import Tenant

    tenant_id = uuid4()
    tenant = Tenant(id=tenant_id, name="No Owner Yet", slug=f"no-owner-{tenant_id.hex[:8]}")
    with SessionLocal() as db:
        set_tenant_context(db, tenant_id)
        db.add(tenant)
        db.flush()
        with pytest.raises(ApiError) as error:
            assert_owner_remains(db, tenant_id)
        assert error.value.status_code == 409
        assert error.value.code == "last_owner"
        db.rollback()



# ---------------------------------------------------------------------------
# RBAC: isolation, membership lifecycle, platform gate, subscription gate
# ---------------------------------------------------------------------------

def test_cross_tenant_access_is_rejected_as_not_found(client):
    tenant_a, _ = register(client)
    tenant_b, _ = register(client)
    id_a, shop_a = tenant_a.json()["tenant_id"], tenant_a.json()["shop_id"]
    id_b, shop_b = tenant_b.json()["tenant_id"], tenant_b.json()["shop_id"]
    headers_a = auth_headers(tenant_a.json())

    assert client.get(f"/api/v1/tenants/{id_b}", headers=headers_a).status_code == 404
    assert client.get(f"/api/v1/tenants/{id_b}/summary", headers=headers_a).status_code == 404
    assert client.get(f"/api/v1/tenants/{id_b}/shops/{shop_b}/batches", headers=headers_a).status_code == 404
    assert invite(client, id_b, headers_a, "intruder@example.test", "cashier", [shop_b]).status_code == 404
    assert client.get(f"/api/v1/tenants/{id_b}/memberships", headers=headers_a).status_code == 404
    # And tenant A's own data remains fully available.
    assert client.get(f"/api/v1/tenants/{id_a}/shops/{shop_a}/summary", headers=headers_a).status_code == 200


def test_shop_assignments_scope_member_access(client):
    result, _ = register(client)
    owner_headers = auth_headers(result.json())
    tenant_id, main_shop = result.json()["tenant_id"], result.json()["shop_id"]
    west_shop = create_second_shop(client, tenant_id, owner_headers, code="WEST")
    headers = invite_and_accept(client, tenant_id, owner_headers,
                                "west-only@example.test", "cashier", [main_shop])
    _, batch = create_inventory(client, tenant_id, owner_headers, main_shop, sku="SCOPE-1")

    assert client.get(f"/api/v1/tenants/{tenant_id}/shops/{main_shop}/batches",
                      headers=headers).status_code == 200
    denied = client.get(f"/api/v1/tenants/{tenant_id}/shops/{west_shop['id']}/batches",
                        headers=headers)
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "shop_access_denied"
    denied_sale = client.post(f"/api/v1/tenants/{tenant_id}/sales", headers=headers,
                              json=sale_payload(west_shop["id"], batch["id"]))
    assert denied_sale.status_code == 403

    discovery = workspace_for(client, headers)
    shops = discovery["workspaces"][0]["shops"]
    assert [shop["shop_id"] for shop in shops] == [str(main_shop)]


def test_deactivated_membership_removes_access_until_reactivated(client):
    result, _ = register(client)
    owner_headers = auth_headers(result.json())
    tenant_id, shop_id = result.json()["tenant_id"], result.json()["shop_id"]
    member_headers = invite_and_accept(client, tenant_id, owner_headers,
                                       "leaver@example.test", "cashier", [shop_id])
    memberships = client.get(f"/api/v1/tenants/{tenant_id}/memberships", headers=owner_headers).json()
    membership = next(row for row in memberships if row["email"] == "leaver@example.test")

    deactivated = client.patch(f"/api/v1/tenants/{tenant_id}/memberships/{membership['id']}",
                               headers=owner_headers)
    assert deactivated.status_code == 200, deactivated.text
    # The person can still sign in, but no workspace or tenant data is visible.
    assert login(client, "leaver@example.test", "another-strong-password").status_code == 200
    assert workspace_for(client, member_headers)["workspaces"] == []
    assert client.get(f"/api/v1/tenants/{tenant_id}", headers=member_headers).status_code == 404

    reactivated = client.patch(
        f"/api/v1/tenants/{tenant_id}/memberships/{membership['id']}/reactivate",
        headers=owner_headers)
    assert reactivated.status_code == 200, reactivated.text
    assert len(workspace_for(client, member_headers)["workspaces"]) == 1


def test_platform_operator_endpoints_reject_non_operators(client):
    result, _ = register(client)
    headers = auth_headers(result.json())
    denied = client.get("/api/v1/platform/tenants", headers=headers)
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "platform_operator_required"
    assert client.get("/api/v1/platform/audit", headers=headers).status_code == 403
    assert client.get("/api/v1/platform/devices", headers=headers).status_code == 403

    operator_reg, _ = register(client, owner_email="owner-platform-grant@example.test")
    assert operator_reg.status_code == 201
    operator_headers = auth_headers(operator_reg.json())
    # A tenant owner is not a platform operator until granted the role.
    assert client.get("/api/v1/platform/tenants", headers=operator_headers).status_code == 403
    with SessionLocal.begin() as db:
        user = db.scalar(select(User).where(User.email == "owner-platform-grant@example.test"))
        db.add(PlatformOperator(user_id=user.id, is_active=True))
    assert client.get("/api/v1/platform/tenants", headers=operator_headers).status_code == 200
    # Platform access never bypasses tenant-scoped membership checks.
    assert client.get(f"/api/v1/tenants/{operator_reg.json()['tenant_id']}/memberships",
                      headers=operator_headers).status_code == 200


def test_inactive_subscription_blocks_write_operations(client):
    result, _ = register(client)
    headers = auth_headers(result.json())
    tenant_id, shop_id = result.json()["tenant_id"], result.json()["shop_id"]
    with SessionLocal.begin() as db:
        set_tenant_context(db, tenant_id)
        subscription = db.scalar(select(TenantSubscription).where(TenantSubscription.tenant_id == tenant_id))
        subscription.status = "expired"
        subscription.ends_at = datetime.now(timezone.utc) - timedelta(days=1)

    denied = client.post(f"/api/v1/tenants/{tenant_id}/purchases", headers=headers, json={
        "shop_id": str(shop_id), "supplier_id": str(uuid4()),
        "transaction_id": str(uuid4()),
        "items": [{"medicine_id": str(uuid4()), "batch_no": "X", "expiry_date": "2030-01-01",
                   "quantity": 1, "purchase_cost": "1.00", "selling_price": "2.00"}],
    })
    assert denied.status_code == 402
    assert denied.json()["error"]["code"] == "subscription_inactive"
    # Read-only access continues to work for existing data.
    assert client.get(f"/api/v1/tenants/{tenant_id}/medicines", headers=headers).status_code == 200










