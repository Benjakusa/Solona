from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.session import SessionLocal, set_tenant_context
from app.models import (Customer, DarajaAttempt, Payment, PlatformOperator, Purchase, Sale,
                        SaleItem, SaleReturn, StockMovement, SubscriptionPayment, SyncEvent,
                        TenantSubscription, User)
from pathlib import Path


def register(client, suffix: str):
    response = client.post("/api/v1/auth/register-tenant", json={
        "tenant_name": f"Pharmacy {suffix}",
        "tenant_slug": f"pharmacy-{suffix}",
        "shop_name": "Main Shop",
        "shop_code": "MAIN",
        "owner_email": f"owner-{suffix}@example.test",
        "owner_name": "Test Owner",
        "owner_password": "a-strong-test-password",
    })
    assert response.status_code == 201, response.text
    result = response.json()
    return result, {"Authorization": f"Bearer {result['access_token']}"}


def create_inventory(client, tenant_id, headers, shop_id, quantity=10):
    medicine_response = client.post(f"/api/v1/tenants/{tenant_id}/medicines", headers=headers, json={"sku": "SKU-001", "name": "Paracetamol", "base_unit": "tablet"})
    assert medicine_response.status_code == 201, medicine_response.text
    supplier_response = client.post(f"/api/v1/tenants/{tenant_id}/suppliers", headers=headers, json={"name": "Local Supplier"})
    assert supplier_response.status_code == 201, supplier_response.text
    purchase_response = client.post(f"/api/v1/tenants/{tenant_id}/purchases", headers=headers, json={
        "shop_id": shop_id,
        "supplier_id": supplier_response.json()["id"],
        "transaction_id": str(uuid4()),
        "items": [{"medicine_id": medicine_response.json()["id"], "batch_no": "BATCH-1", "expiry_date": "2030-10-31", "quantity": quantity, "purchase_cost": "3.25", "selling_price": "5.00"}],
    })
    assert purchase_response.status_code == 201, purchase_response.text
    return medicine_response.json(), supplier_response.json()


def test_two_tenants_and_multiple_shops_are_isolated(client):
    tenant_a, headers_a = register(client, "alpha")
    tenant_b, headers_b = register(client, "bravo")
    response = client.post(f"/api/v1/tenants/{tenant_a['tenant_id']}/shops", headers=headers_a, json={"name": "West Shop", "code": "WEST", "timezone": "Africa/Nairobi", "receipt_settings": {"paper_width_mm": 80}})
    assert response.status_code == 201, response.text
    assert client.get(f"/api/v1/tenants/{tenant_a['tenant_id']}/shops", headers=headers_a).status_code == 200
    cross_tenant = client.get(f"/api/v1/tenants/{tenant_b['tenant_id']}/summary", headers=headers_a)
    assert cross_tenant.status_code == 404
    assert client.get(f"/api/v1/tenants/{tenant_b['tenant_id']}/summary", headers=headers_b).status_code == 200
    invite = client.post(f"/api/v1/tenants/{tenant_b['tenant_id']}/invitations", headers=headers_b, json={"email": "owner-alpha@example.test", "role": "cashier", "shop_ids": [tenant_b["shop_id"]]})
    assert invite.status_code == 201, invite.text
    accepted = client.post("/api/v1/auth/accept-invitation", headers=headers_a, json={"token": invite.json()["invitation_token"], "full_name": "Test Owner"})
    assert accepted.status_code == 200, accepted.text
    assert client.get(f"/api/v1/tenants/{tenant_b['tenant_id']}", headers=headers_a).status_code == 200


def test_shop_assignment_limits_user_and_summary_scope(client):
    owner, owner_headers = register(client, "shop-scope")
    tenant_id = owner["tenant_id"]
    shop_a = owner["shop_id"]
    shop_b_response = client.post(f"/api/v1/tenants/{tenant_id}/shops", headers=owner_headers, json={"name": "Second Shop", "code": "SECOND"})
    assert shop_b_response.status_code == 201, shop_b_response.text
    shop_b = shop_b_response.json()["id"]
    invite = client.post(f"/api/v1/tenants/{tenant_id}/invitations", headers=owner_headers, json={"email": "cashier@example.test", "role": "cashier", "shop_ids": [shop_a]})
    assert invite.status_code == 201, invite.text
    accepted = client.post("/api/v1/auth/accept-invitation", json={"token": invite.json()["invitation_token"], "full_name": "Shop Cashier", "password": "another-strong-test-password"})
    assert accepted.status_code == 200, accepted.text
    login = client.post("/api/v1/auth/login", json={"email": "cashier@example.test", "password": "another-strong-test-password"})
    cashier_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert client.get(f"/api/v1/tenants/{tenant_id}/shops/{shop_a}/summary", headers=cashier_headers).status_code == 200
    assert client.get(f"/api/v1/tenants/{tenant_id}/shops/{shop_b}/summary", headers=cashier_headers).status_code == 403
    summary = client.get(f"/api/v1/tenants/{tenant_id}/summary", headers=cashier_headers)
    assert summary.status_code == 200
    assert summary.json()["shop_count"] == 1
    inventory_invite = client.post(f"/api/v1/tenants/{tenant_id}/invitations", headers=owner_headers, json={"email": "stock-manager@example.test", "role": "inventory_manager", "shop_ids": [shop_a]})
    assert inventory_invite.status_code == 201
    accepted_inventory = client.post("/api/v1/auth/accept-invitation", json={"token": inventory_invite.json()["invitation_token"], "full_name": "Stock Manager", "password": "yet-another-strong-test-password"})
    assert accepted_inventory.status_code == 200
    manager_login = client.post("/api/v1/auth/login", json={"email": "stock-manager@example.test", "password": "yet-another-strong-test-password"})
    manager_headers = {"Authorization": f"Bearer {manager_login.json()['access_token']}"}
    assert client.get(f"/api/v1/tenants/{tenant_id}/summary", headers=manager_headers).status_code == 403


def test_batch_stock_sale_and_historical_cost_are_preserved(client):
    tenant, headers = register(client, "batch-sale")
    medicine, _ = create_inventory(client, tenant["tenant_id"], headers, tenant["shop_id"], quantity=8)
    batch = client.get(f"/api/v1/tenants/{tenant['tenant_id']}/shops/{tenant['shop_id']}/batches", headers=headers).json()[0]
    sale = client.post(f"/api/v1/tenants/{tenant['tenant_id']}/sales", headers=headers, json={
        "shop_id": tenant["shop_id"], "transaction_id": str(uuid4()), "invoice_no": "INV-1",
        "items": [{"batch_id": batch["id"], "quantity": 3}], "payment_method": "cash", "amount_paid": "15.00",
    })
    assert sale.status_code == 201, sale.text
    assert Decimal(str(sale.json()["total_amount"])) == Decimal("15.00")
    refreshed = client.get(f"/api/v1/tenants/{tenant['tenant_id']}/shops/{tenant['shop_id']}/batches", headers=headers).json()[0]
    assert refreshed["quantity"] == 5
    with SessionLocal() as db:
        set_tenant_context(db, tenant["tenant_id"])
        stock_moves = db.scalars(select(StockMovement).where(StockMovement.tenant_id == tenant["tenant_id"])).all()
        assert sorted(move.quantity_delta for move in stock_moves) == [-3, 8]
        sale_item = db.execute(select(SaleItem)).scalar_one()
        assert sale_item.unit_cost == Decimal("3.25")


def test_failed_purchase_rolls_back_header_and_prior_lines(client):
    tenant, headers = register(client, "rollback")
    medicine, supplier = create_inventory(client, tenant["tenant_id"], headers, tenant["shop_id"], quantity=2)
    before = client.get(f"/api/v1/tenants/{tenant['tenant_id']}/shops/{tenant['shop_id']}/batches", headers=headers).json()
    with SessionLocal() as db:
        set_tenant_context(db, tenant["tenant_id"])
        purchase_count_before = len(db.scalars(select(Purchase.id).where(Purchase.tenant_id == tenant["tenant_id"])).all())
    result = client.post(f"/api/v1/tenants/{tenant['tenant_id']}/purchases", headers=headers, json={
        "shop_id": tenant["shop_id"], "supplier_id": supplier["id"], "transaction_id": str(uuid4()),
        "items": [
            {"medicine_id": medicine["id"], "batch_no": "BATCH-2", "expiry_date": "2031-01-31", "quantity": 4, "purchase_cost": "2.00", "selling_price": "4.00"},
            {"medicine_id": str(uuid4()), "batch_no": "BATCH-X", "expiry_date": "2031-01-31", "quantity": 4, "purchase_cost": "2.00", "selling_price": "4.00"},
        ],
    })
    assert result.status_code == 404
    after = client.get(f"/api/v1/tenants/{tenant['tenant_id']}/shops/{tenant['shop_id']}/batches", headers=headers).json()
    assert after == before
    with SessionLocal() as db:
        set_tenant_context(db, tenant["tenant_id"])
        purchase_count_after = len(db.scalars(select(Purchase.id).where(Purchase.tenant_id == tenant["tenant_id"])).all())
        assert purchase_count_after == purchase_count_before


def test_duplicate_sku_and_cross_shop_foreign_key_are_rejected(client):
    tenant_a, headers_a = register(client, "fk-a")
    tenant_b, headers_b = register(client, "fk-b")
    medicine_a, supplier_a = create_inventory(client, tenant_a["tenant_id"], headers_a, tenant_a["shop_id"])
    cross_shop_request = client.post(f"/api/v1/tenants/{tenant_a['tenant_id']}/purchases", headers=headers_a, json={
        "shop_id": tenant_b["shop_id"], "supplier_id": supplier_a["id"], "transaction_id": str(uuid4()),
        "items": [{"medicine_id": medicine_a["id"], "batch_no": "BATCH-X", "expiry_date": "2031-01-31", "quantity": 1, "purchase_cost": "2.00", "selling_price": "3.00"}],
    })
    assert cross_shop_request.status_code == 404
    duplicate = client.post(f"/api/v1/tenants/{tenant_a['tenant_id']}/medicines", headers=headers_a, json={"sku": "SKU-001", "name": "Duplicate"})
    assert duplicate.status_code == 409
    with SessionLocal() as db:
        set_tenant_context(db, tenant_a["tenant_id"])
        invalid = Purchase(tenant_id=UUID(tenant_a["tenant_id"]), shop_id=UUID(tenant_b["shop_id"]), supplier_id=UUID(supplier_a["id"]), transaction_id=uuid4())
        db.add(invalid)
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()


def test_concurrent_sales_cannot_oversell_a_locked_batch(client):
    tenant, headers = register(client, "concurrency")
    create_inventory(client, tenant["tenant_id"], headers, tenant["shop_id"], quantity=5)
    batch = client.get(f"/api/v1/tenants/{tenant['tenant_id']}/shops/{tenant['shop_id']}/batches", headers=headers).json()[0]

    def sell(number):
        return client.post(f"/api/v1/tenants/{tenant['tenant_id']}/sales", headers=headers, json={
            "shop_id": tenant["shop_id"], "transaction_id": str(uuid4()), "invoice_no": f"CON-{number}",
            "items": [{"batch_id": batch["id"], "quantity": 4}], "payment_method": "cash", "amount_paid": "20.00",
        }).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(sell, (1, 2)))
    assert sorted(statuses) == [201, 409]
    remaining = client.get(f"/api/v1/tenants/{tenant['tenant_id']}/shops/{tenant['shop_id']}/batches", headers=headers).json()[0]["quantity"]
    assert remaining == 1


def test_refresh_rotation_and_reuse_revokes_the_family(client):
    tenant, _ = register(client, "refresh")
    login = client.post("/api/v1/auth/login", json={"email": "owner-refresh@example.test", "password": "a-strong-test-password"})
    old_refresh = login.json()["refresh_token"]
    rotated = client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert rotated.status_code == 200
    new_refresh = rotated.json()["refresh_token"]
    reused = client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert reused.status_code == 401
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": new_refresh}).status_code == 401


def test_expired_subscription_preserves_read_access_but_blocks_writes(client):
    tenant, headers = register(client, "subscription")
    with SessionLocal() as db:
        set_tenant_context(db, tenant["tenant_id"])
        subscription = db.scalar(select(TenantSubscription).where(TenantSubscription.tenant_id == tenant["tenant_id"]))
        subscription.status = "expired"
        db.commit()
    response = client.get(f"/api/v1/tenants/{tenant['tenant_id']}/summary", headers=headers)
    assert response.status_code == 200
    denied = client.post(f"/api/v1/tenants/{tenant['tenant_id']}/medicines", headers=headers,
                         json={"sku": "BLOCKED", "name": "Blocked write"})
    assert denied.status_code == 402


def test_daraja_callback_never_confirms_payment_and_query_reconciliation_is_idempotent(client, monkeypatch):
    from pydantic import SecretStr
    from app.core.config import get_settings
    from app.services import daraja

    settings = get_settings()
    settings.daraja_environment = "sandbox"
    settings.daraja_consumer_key = SecretStr("test-key")
    settings.daraja_consumer_secret = SecretStr("test-secret")
    settings.daraja_shortcode = "174379"
    settings.daraja_passkey = SecretStr("test-passkey")
    settings.daraja_callback_base_url = "https://example.test"
    settings.daraja_party_b = "174379"
    monkeypatch.setattr(daraja, "access_token", lambda: "sandbox-token")
    query_result = {"ResponseCode": "0", "CheckoutRequestID": "ws_CO_test_1",
                    "MerchantRequestID": "merchant_1", "ResultCode": "4999",
                    "ResultDesc": "Transaction still under processing"}
    query_timeout = [False]

    def fake_request(url, **kwargs):
        if url.endswith("/processrequest"):
            return {"ResponseCode": "0", "CheckoutRequestID": "ws_CO_test_1",
                    "MerchantRequestID": "merchant_1"}
        if query_timeout[0]:
            raise daraja.DarajaError("Daraja is unavailable or returned an invalid response.")
        return dict(query_result)

    monkeypatch.setattr(daraja, "_request_json", fake_request)

    tenant, headers = register(client, "daraja")
    with SessionLocal() as db:
        set_tenant_context(db, tenant["tenant_id"])
        subscription = db.scalar(select(TenantSubscription).where(TenantSubscription.tenant_id == tenant["tenant_id"]))
        plan_id = subscription.plan_id
        from app.models import SubscriptionPlan
        db.get(SubscriptionPlan, plan_id).monthly_price = Decimal("500.00")
        db.commit()
    started = client.post(f"/api/v1/tenants/{tenant['tenant_id']}/billing/stk-push", headers=headers,
                          json={"phone_number": "0712345678", "idempotency_key": "invoice_attempt_001"})
    assert started.status_code == 202, started.text
    assert started.json()["status"] == "pending"
    duplicate_request = client.post(f"/api/v1/tenants/{tenant['tenant_id']}/billing/stk-push", headers=headers,
                                   json={"phone_number": "0712345678", "idempotency_key": "invoice_attempt_001"})
    assert duplicate_request.status_code == 202
    assert duplicate_request.json()["payment_id"] == started.json()["payment_id"]

    callback = {"Body": {"stkCallback": {
        "MerchantRequestID": "attacker_supplied_reference", "CheckoutRequestID": "ws_CO_test_1",
        "ResultCode": 0, "ResultDesc": "success",
        "CallbackMetadata": {"Item": [
            {"Name": "Amount", "Value": 1}, {"Name": "MpesaReceiptNumber", "Value": "FORGED123"},
            {"Name": "PhoneNumber", "Value": 254700000000},
        ]},
    }}}
    first = client.post("/api/v1/payments/daraja/callback", json=callback)
    second = client.post("/api/v1/payments/daraja/callback", json=callback)
    assert first.status_code == second.status_code == 200
    with SessionLocal() as db:
        set_tenant_context(db, tenant["tenant_id"])
        payment = db.scalar(select(SubscriptionPayment).where(SubscriptionPayment.id == UUID(started.json()["payment_id"])))
        attempt = db.scalar(select(DarajaAttempt).where(DarajaAttempt.subscription_payment_id == payment.id))
        subscription = db.scalar(select(TenantSubscription).where(TenantSubscription.tenant_id == tenant["tenant_id"]))
        assert payment.status == "pending"
        assert attempt.status == "reconciliation_required"
        assert attempt.mpesa_receipt is None
        assert "FORGED123" not in str(attempt.callback_payload)
        assert subscription.status == "trialing"
        original_end = subscription.ends_at

    # Provider ambiguity is never interpreted as either a payment or a final failure.
    reconcile_url = (f"/api/v1/tenants/{tenant['tenant_id']}/billing/payments/"
                     f"{started.json()['payment_id']}/reconcile")
    query_timeout[0] = True
    timed_out = client.post(reconcile_url, headers=headers)
    query_timeout[0] = False
    assert timed_out.status_code == 503
    pending = client.post(reconcile_url, headers=headers)
    assert pending.status_code == 200
    assert pending.json()["payment_status"] == "reconciliation_required"
    assert pending.json()["reconciliation_required"] is True

    # A sandbox checkout cannot be reconciled against a production endpoint/account.
    settings.daraja_environment = "production"
    wrong_environment = client.post(reconcile_url, headers=headers)
    settings.daraja_environment = "sandbox"
    assert wrong_environment.status_code == 200
    assert wrong_environment.json()["payment_status"] == "reconciliation_required"
    settings.daraja_shortcode = "999999"
    wrong_account = client.post(reconcile_url, headers=headers)
    settings.daraja_shortcode = "174379"
    assert wrong_account.status_code == 200
    assert wrong_account.json()["payment_status"] == "reconciliation_required"

    # An uncorrelated provider response cannot authorize entitlement.
    query_result.update({"CheckoutRequestID": "different_checkout", "ResultCode": "0"})
    mismatched = client.post(reconcile_url, headers=headers)
    assert mismatched.status_code == 200
    assert mismatched.json()["payment_status"] == "reconciliation_required"

    # Daraja STK Push Query independently verifies the exact checkout and merchant IDs.
    query_result.update({"CheckoutRequestID": "ws_CO_test_1", "ResultCode": "0",
                         "ResultDesc": "The service request is processed successfully"})
    verified = client.post(reconcile_url, headers=headers)
    assert verified.status_code == 200
    assert verified.json()["payment_status"] == "paid"
    assert verified.json()["reconciliation_required"] is False
    with SessionLocal() as db:
        set_tenant_context(db, tenant["tenant_id"])
        payment = db.scalar(select(SubscriptionPayment).where(SubscriptionPayment.id == UUID(started.json()["payment_id"])))
        attempt = db.scalar(select(DarajaAttempt).where(DarajaAttempt.subscription_payment_id == payment.id))
        subscription = db.scalar(select(TenantSubscription).where(TenantSubscription.tenant_id == tenant["tenant_id"]))
        assert payment.status == attempt.status == "paid"
        assert attempt.mpesa_receipt is None  # STK Push Query does not return a receipt.
        assert subscription.status == "active"
        paid_end = subscription.ends_at
        assert paid_end > original_end

    # Reconciliation retries and callback replays do not grant extra subscription time.
    duplicate_reconcile = client.post(reconcile_url, headers=headers)
    duplicate_callback = client.post("/api/v1/payments/daraja/callback", json=callback)
    assert duplicate_reconcile.status_code == duplicate_callback.status_code == 200
    with SessionLocal() as db:
        set_tenant_context(db, tenant["tenant_id"])
        payment = db.scalar(select(SubscriptionPayment).where(SubscriptionPayment.id == UUID(started.json()["payment_id"])))
        attempt = db.scalar(select(DarajaAttempt).where(DarajaAttempt.subscription_payment_id == payment.id))
        subscription = db.scalar(select(TenantSubscription).where(TenantSubscription.tenant_id == tenant["tenant_id"]))
        assert payment.status == "paid"
        assert attempt.status == "paid"
        assert subscription.ends_at == paid_end


def test_platform_routes_require_explicit_operator_and_support_grant(client):
    tenant, owner_headers = register(client, "platform-ops")
    forbidden = client.get("/api/v1/platform/tenants", headers=owner_headers)
    assert forbidden.status_code == 403
    with SessionLocal.begin() as db:
        user = db.scalar(select(User).where(User.email == "owner-platform-ops@example.test"))
        db.add(PlatformOperator(user_id=user.id, is_active=True))
        operator_id = user.id
    login = client.post("/api/v1/auth/login", json={"email": "owner-platform-ops@example.test", "password": "a-strong-test-password"})
    operator_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert client.get("/api/v1/platform/tenants", headers=operator_headers).status_code == 200
    grant = client.post(f"/api/v1/platform/tenants/{tenant['tenant_id']}/support-access-grants",
                        headers=operator_headers, json={"reason": "Investigate a customer reported sync incident", "duration_minutes": 15})
    assert grant.status_code == 201, grant.text
    audit = client.get(f"/api/v1/platform/tenants/{tenant['tenant_id']}/audit", headers=operator_headers)
    assert audit.status_code == 200, audit.text
    assert all("details" not in row for row in audit.json())
    revoked = client.delete(f"/api/v1/platform/support-access-grants/{grant.json()['id']}", headers=operator_headers)
    assert revoked.status_code == 200


def test_device_credentials_are_one_time_and_revocation_is_visible(client):
    tenant, headers = register(client, "device")
    response = client.post(f"/api/v1/tenants/{tenant['tenant_id']}/devices", headers=headers, json={"shop_id": tenant["shop_id"], "name": "Till 1", "platform": "windows"})
    assert response.status_code == 201, response.text
    credential = response.json()["device_credential"]
    assert len(credential) > 40
    devices = client.get(f"/api/v1/tenants/{tenant['tenant_id']}/devices", headers=headers).json()
    assert "device_credential" not in devices[0]
    revoked = client.delete(f"/api/v1/tenants/{tenant['tenant_id']}/devices/{response.json()['id']}", headers=headers)
    assert revoked.status_code == 200
    assert client.get(f"/api/v1/tenants/{tenant['tenant_id']}/devices", headers=headers).json()[0]["revoked_at"] is not None


def test_workspace_discovery_and_device_sale_replay_are_scoped_and_idempotent(client):
    tenant, user_headers = register(client, "sync-replay")
    workspace_response = client.get("/api/v1/auth/workspaces", headers=user_headers)
    assert workspace_response.status_code == 200, workspace_response.text
    workspace = workspace_response.json()["workspaces"][0]
    assert workspace["tenant_id"] == tenant["tenant_id"]
    device_response = client.post(
        f"/api/v1/tenants/{tenant['tenant_id']}/devices", headers=user_headers,
        json={"shop_id": tenant["shop_id"], "name": "Offline Till", "platform": "test"},
    )
    assert device_response.status_code == 201, device_response.text
    device = device_response.json()
    medicine, supplier = create_inventory(client, tenant["tenant_id"], user_headers, tenant["shop_id"], quantity=5)
    batch = client.get(
        f"/api/v1/tenants/{tenant['tenant_id']}/shops/{tenant['shop_id']}/batches", headers=user_headers,
    ).json()[0]
    customer_response = client.post(
        f"/api/v1/tenants/{tenant['tenant_id']}/customers", headers=user_headers,
        json={"name": "Offline credit customer", "phone": "0712345678"},
    )
    assert customer_response.status_code == 201
    from datetime import datetime, timezone

    event = {
        "event_id": str(uuid4()), "transaction_id": str(uuid4()),
        "tenant_id": tenant["tenant_id"], "shop_id": tenant["shop_id"], "device_id": device["id"],
        "event_type": "sale.created", "aggregate_id": "offline-invoice", "schema_version": 1,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "payload": {"invoice_no": "OFFLINE-INV-1", "cashier_cloud_id": workspace_response.json()["user_id"],
                    "cashier_name": "Test Owner", "customer_cloud_id": customer_response.json()["id"],
                    "total_amount": 9, "discount": 2, "tax": 1, "credit_amount": 5,
                    "amount_paid": 4, "payment_id": str(uuid4()), "payment_method": "cash", "lines": [{
                        "cloud_medicine_id": medicine["id"], "cloud_batch_id": batch["id"],
                        "cloud_sale_item_id": str(uuid4()), "quantity": 2, "unit_price": 5,
                        "unit_cost": 3.25, "subtotal": 10,
                    }]},
    }
    url = f"/api/v1/tenants/{tenant['tenant_id']}/shops/{tenant['shop_id']}/sync/events"
    headers = {"X-Device-ID": device["id"], "X-Device-Secret": device["device_credential"]}
    first = client.post(url, headers=headers, json={"events": [event]})
    assert first.status_code == 200, first.text
    assert first.json()["events"][0]["status"] == "applied"
    sale_id = event["transaction_id"]
    repeated = client.post(url, headers=headers, json={"events": [event]})
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["events"][0]["duplicate"] is True
    changed = json.loads(json.dumps(event))
    changed["payload"]["total_amount"] = 8
    changed_response = client.post(url, headers=headers, json={"events": [changed]})
    assert changed_response.status_code == 409
    inconsistent = json.loads(json.dumps(event))
    inconsistent["event_id"] = str(uuid4())
    inconsistent["transaction_id"] = str(uuid4())
    inconsistent["payload"]["invoice_no"] = "OFFLINE-INV-BAD-TOTAL"
    inconsistent["payload"]["total_amount"] = 11
    inconsistent["payload"]["payment_id"] = str(uuid4())
    inconsistent["payload"]["lines"][0]["cloud_sale_item_id"] = str(uuid4())
    rejected_total = client.post(url, headers=headers, json={"events": [inconsistent]})
    assert rejected_total.status_code == 200
    assert rejected_total.json()["events"][0]["status"] == "conflict"
    remaining = client.get(
        f"/api/v1/tenants/{tenant['tenant_id']}/shops/{tenant['shop_id']}/batches", headers=user_headers,
    ).json()[0]["quantity"]
    assert remaining == 3
    with SessionLocal() as db:
        set_tenant_context(db, tenant["tenant_id"])
        sale = db.get(Sale, UUID(sale_id))
        item = db.scalar(select(SaleItem).where(SaleItem.sale_id == sale.id))
        payment = db.scalar(select(Payment).where(Payment.sale_id == sale.id))
        customer = db.get(Customer, UUID(customer_response.json()["id"]))
        movements = db.scalars(select(StockMovement).where(
            StockMovement.transaction_id == UUID(event["transaction_id"]),
        )).all()
        assert sale.total_amount == Decimal("9.00")
        assert sale.discount_amount == Decimal("2.00")
        assert sale.tax_amount == Decimal("1.00")
        assert sale.amount_paid == Decimal("4.00")
        assert item.id == UUID(event["payload"]["lines"][0]["cloud_sale_item_id"])
        assert item.unit_price == Decimal("5.00") and item.unit_cost == Decimal("3.25")
        assert payment.id == UUID(event["payload"]["payment_id"])
        assert payment.amount == Decimal("4.00")
        assert customer.credit_balance == Decimal("5.00")
        assert len(movements) == 1 and movements[0].quantity_delta == -2
    summary = client.get(f"/api/v1/tenants/{tenant['tenant_id']}/shops/{tenant['shop_id']}/summary", headers=user_headers)
    assert summary.json()["sales_count"] == 1
    second_device_response = client.post(
        f"/api/v1/tenants/{tenant['tenant_id']}/devices", headers=user_headers,
        json={"shop_id": tenant["shop_id"], "name": "Second till", "platform": "test"},
    )
    second_device = second_device_response.json()
    scoped = f"/api/v1/tenants/{tenant['tenant_id']}/shops/{tenant['shop_id']}/sync"
    second_headers = {"X-Device-ID": second_device["id"], "X-Device-Secret": second_device["device_credential"]}
    bootstrap = client.get(f"{scoped}/bootstrap", headers=second_headers)
    assert bootstrap.status_code == 200, bootstrap.text
    assert bootstrap.json()["medicines"][0]["id"] == medicine["id"]
    assert bootstrap.json()["batches"][0]["quantity"] == 3
    changes = client.get(f"{scoped}/changes?cursor=0", headers=second_headers)
    assert changes.status_code == 200, changes.text
    assert changes.json()["changes"][0]["event_id"] == event["event_id"]

    # Ordinary fully paid cash replay remains equivalent to the local sale path.
    cash_event = {
        "event_id": str(uuid4()), "transaction_id": str(uuid4()),
        "tenant_id": tenant["tenant_id"], "shop_id": tenant["shop_id"], "device_id": second_device["id"],
        "event_type": "sale.created", "aggregate_id": "offline-cash", "schema_version": 1,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "payload": {"invoice_no": "OFFLINE-CASH-1", "cashier_cloud_id": workspace_response.json()["user_id"],
                    "total_amount": 5, "discount": 0, "tax": 0, "credit_amount": 0,
                    "amount_paid": 5, "payment_id": str(uuid4()), "payment_method": "cash", "lines": [{
                        "cloud_medicine_id": medicine["id"], "cloud_batch_id": batch["id"],
                        "cloud_sale_item_id": str(uuid4()), "quantity": 1, "unit_price": 5,
                        "unit_cost": 3.25, "subtotal": 5,
                    }]},
    }
    cash_replay = client.post(url, headers=second_headers, json={"events": [cash_event]})
    assert cash_replay.status_code == 200, cash_replay.text
    assert cash_replay.json()["events"][0]["status"] == "applied"
    with SessionLocal() as db:
        set_tenant_context(db, tenant["tenant_id"])
        cash_sale = db.get(Sale, UUID(cash_event["transaction_id"]))
        cash_payment = db.scalar(select(Payment).where(Payment.sale_id == cash_sale.id))
        assert cash_sale.total_amount == cash_sale.amount_paid == Decimal("5.00")
        assert cash_sale.discount_amount == cash_sale.tax_amount == Decimal("0.00")
        assert cash_payment.id == UUID(cash_event["payload"]["payment_id"])


def test_device_sync_flags_stale_stock_and_rejects_revoked_devices(client):
    tenant, user_headers = register(client, "sync-conflict")
    device_response = client.post(
        f"/api/v1/tenants/{tenant['tenant_id']}/devices", headers=user_headers,
        json={"shop_id": tenant["shop_id"], "name": "Offline Till", "platform": "test"},
    )
    device = device_response.json()
    workspace = client.get("/api/v1/auth/workspaces", headers=user_headers).json()
    medicine, _ = create_inventory(client, tenant["tenant_id"], user_headers, tenant["shop_id"], quantity=3)
    batch = client.get(f"/api/v1/tenants/{tenant['tenant_id']}/shops/{tenant['shop_id']}/batches", headers=user_headers).json()[0]
    online_sale = client.post(f"/api/v1/tenants/{tenant['tenant_id']}/sales", headers=user_headers, json={
        "shop_id": tenant["shop_id"], "transaction_id": str(uuid4()), "invoice_no": "ONLINE-1",
        "items": [{"batch_id": batch["id"], "quantity": 2}], "payment_method": "cash", "amount_paid": "10.00",
    })
    assert online_sale.status_code == 201
    from datetime import datetime, timezone

    event = {"event_id": str(uuid4()), "transaction_id": str(uuid4()),
             "tenant_id": tenant["tenant_id"], "shop_id": tenant["shop_id"], "device_id": device["id"],
             "event_type": "sale.created", "aggregate_id": "offline-conflict", "schema_version": 1,
             "occurred_at": datetime.now(timezone.utc).isoformat(),
             "payload": {"invoice_no": "OFFLINE-2", "cashier_cloud_id": workspace["user_id"],
                         "total_amount": 10, "amount_paid": 10, "payment_method": "cash", "lines": [{
                             "cloud_medicine_id": medicine["id"], "cloud_batch_id": batch["id"],
                             "cloud_sale_item_id": str(uuid4()), "quantity": 2, "unit_price": 5,
                             "unit_cost": 3.25, "subtotal": 10,
                         }]}}
    sync_url = f"/api/v1/tenants/{tenant['tenant_id']}/shops/{tenant['shop_id']}/sync/events"
    device_headers = {"X-Device-ID": device["id"], "X-Device-Secret": device["device_credential"]}
    conflict = client.post(sync_url, headers=device_headers, json={"events": [event]})
    assert conflict.status_code == 200, conflict.text
    assert conflict.json()["events"][0]["status"] == "conflict"
    client.delete(f"/api/v1/tenants/{tenant['tenant_id']}/devices/{device['id']}", headers=user_headers)
    denied = client.post(sync_url, headers=device_headers, json={"events": [event]})
    assert denied.status_code == 401


def test_offline_sale_replay_rolls_back_financial_and_stock_writes_on_failure(client, monkeypatch):
    from datetime import datetime, timezone
    from app.services import pharmacy

    tenant, user_headers = register(client, "sync-rollback")
    workspace = client.get("/api/v1/auth/workspaces", headers=user_headers).json()
    device_response = client.post(
        f"/api/v1/tenants/{tenant['tenant_id']}/devices", headers=user_headers,
        json={"shop_id": tenant["shop_id"], "name": "Rollback till", "platform": "test"},
    )
    device = device_response.json()
    medicine, _ = create_inventory(client, tenant["tenant_id"], user_headers, tenant["shop_id"], quantity=4)
    batch = client.get(
        f"/api/v1/tenants/{tenant['tenant_id']}/shops/{tenant['shop_id']}/batches", headers=user_headers,
    ).json()[0]
    event = {
        "event_id": str(uuid4()), "transaction_id": str(uuid4()),
        "tenant_id": tenant["tenant_id"], "shop_id": tenant["shop_id"], "device_id": device["id"],
        "event_type": "sale.created", "aggregate_id": "offline-rollback", "schema_version": 1,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "payload": {"invoice_no": "OFFLINE-ROLLBACK", "cashier_cloud_id": workspace["user_id"],
                    "total_amount": 10, "discount": 0, "tax": 0, "credit_amount": 0,
                    "amount_paid": 10, "payment_id": str(uuid4()), "payment_method": "cash", "lines": [{
                        "cloud_medicine_id": medicine["id"], "cloud_batch_id": batch["id"],
                        "cloud_sale_item_id": str(uuid4()), "quantity": 2, "unit_price": 5,
                        "unit_cost": 3.25, "subtotal": 10,
                    }]},
    }
    url = f"/api/v1/tenants/{tenant['tenant_id']}/shops/{tenant['shop_id']}/sync/events"
    device_headers = {"X-Device-ID": device["id"], "X-Device-Secret": device["device_credential"]}
    original_record_audit = pharmacy.record_audit

    def fail_after_sale_writes(db, **kwargs):
        if kwargs.get("action") == "sale.reconciled_offline":
            raise RuntimeError("injected transaction failure")
        return original_record_audit(db, **kwargs)

    monkeypatch.setattr(pharmacy, "record_audit", fail_after_sale_writes)
    with pytest.raises(RuntimeError, match="injected transaction failure"):
        client.post(url, headers=device_headers, json={"events": [event]})

    with SessionLocal() as db:
        set_tenant_context(db, tenant["tenant_id"])
        assert db.get(Sale, UUID(event["transaction_id"])) is None
        assert db.scalar(select(Payment.id).where(Payment.id == UUID(event["payload"]["payment_id"]))) is None
        assert db.scalar(select(SyncEvent.id).where(SyncEvent.event_id == UUID(event["event_id"]))) is None
        assert db.scalar(select(StockMovement.id).where(
            StockMovement.transaction_id == UUID(event["transaction_id"]),
        )) is None
    current = client.get(
        f"/api/v1/tenants/{tenant['tenant_id']}/shops/{tenant['shop_id']}/batches", headers=user_headers,
    ).json()[0]
    assert current["quantity"] == 4


def test_offline_return_replay_is_audited_idempotent_and_reduces_customer_credit(client, monkeypatch):
    tenant, user_headers = register(client, "return-replay")
    workspace = client.get("/api/v1/auth/workspaces", headers=user_headers).json()
    device = client.post(
        f"/api/v1/tenants/{tenant['tenant_id']}/devices", headers=user_headers,
        json={"shop_id": tenant["shop_id"], "name": "Return terminal", "platform": "test"},
    ).json()
    device_headers = {"X-Device-ID": device["id"], "X-Device-Secret": device["device_credential"]}
    create_inventory(client, tenant["tenant_id"], user_headers, tenant["shop_id"], quantity=4)
    batch = client.get(
        f"/api/v1/tenants/{tenant['tenant_id']}/shops/{tenant['shop_id']}/batches", headers=user_headers,
    ).json()[0]
    customer = client.post(
        f"/api/v1/tenants/{tenant['tenant_id']}/customers", headers=user_headers,
        json={"name": "Return credit customer"},
    ).json()
    sale_transaction_id, line_id = uuid4(), uuid4()
    sale_event = {
        "event_id": str(uuid4()), "transaction_id": str(sale_transaction_id),
        "tenant_id": tenant["tenant_id"], "shop_id": tenant["shop_id"], "device_id": device["id"],
        "event_type": "sale.created", "aggregate_id": "return-sale", "schema_version": 1,
        "occurred_at": "2026-10-12T10:00:00Z",
        "payload": {"invoice_no": "RETURN-INV-1", "cashier_cloud_id": workspace["user_id"],
                    "customer_cloud_id": customer["id"], "total_amount": 10, "discount": 0,
                    "tax": 0, "credit_amount": 10, "amount_paid": 0, "payment_method": "credit",
                    "lines": [{"cloud_medicine_id": batch["medicine_id"], "cloud_batch_id": batch["id"],
                               "cloud_sale_item_id": str(line_id), "quantity": 2,
                               "unit_price": 5, "unit_cost": 3.25, "subtotal": 10}]},
    }
    url = f"/api/v1/tenants/{tenant['tenant_id']}/shops/{tenant['shop_id']}/sync/events"
    assert client.post(url, headers=device_headers, json={"events": [sale_event]}).json()["events"][0]["status"] == "applied"

    def return_event():
        return {
            "event_id": str(uuid4()), "transaction_id": str(uuid4()),
            "tenant_id": tenant["tenant_id"], "shop_id": tenant["shop_id"], "device_id": device["id"],
            "event_type": "sale.returned", "aggregate_id": str(sale_transaction_id), "schema_version": 1,
            "occurred_at": "2026-10-12T10:05:00Z",
            "payload": {"sale_transaction_id": str(sale_transaction_id),
                        "cloud_sale_item_id": str(line_id), "cloud_batch_id": batch["id"],
                        "cashier_cloud_id": workspace["user_id"], "quantity": 1,
                        "refund_amount": 5, "credit_reduction": 5, "reason": "Customer returned sealed item"},
        }

    # A failure after stock/credit writes still rolls the return and inbox back.
    from app.services import pharmacy as pharmacy_service

    original_record_audit = pharmacy_service.record_audit

    def fail_return_audit(db, **kwargs):
        if kwargs.get("action") == "sale.returned":
            raise RuntimeError("injected return transaction failure")
        return original_record_audit(db, **kwargs)

    rolled_back = return_event()
    monkeypatch.setattr(pharmacy_service, "record_audit", fail_return_audit)
    with pytest.raises(RuntimeError, match="injected return transaction failure"):
        client.post(url, headers=device_headers, json={"events": [rolled_back]})
    monkeypatch.setattr(pharmacy_service, "record_audit", original_record_audit)
    with SessionLocal() as db:
        set_tenant_context(db, tenant["tenant_id"])
        item_before = db.get(SaleItem, line_id)
        customer_before = db.get(Customer, UUID(customer["id"]))
        assert item_before.returned_qty == 0
        assert customer_before.credit_balance == Decimal("10.00")
        assert db.scalar(select(SaleReturn.id).where(SaleReturn.transaction_id == UUID(rolled_back["transaction_id"]))) is None
        assert db.scalar(select(SyncEvent.id).where(SyncEvent.event_id == UUID(rolled_back["event_id"]))) is None

    first_return = return_event()
    first_ack = client.post(url, headers=device_headers, json={"events": [first_return]}).json()["events"][0]
    assert first_ack["status"] == "applied"
    repeated = client.post(url, headers=device_headers, json={"events": [first_return]}).json()["events"][0]
    assert repeated["duplicate"] is True
    altered = json.loads(json.dumps(first_return))
    altered["payload"]["refund_amount"] = 4
    assert client.post(url, headers=device_headers, json={"events": [altered]}).status_code == 409

    mismatched_amount = return_event()
    mismatched_amount["payload"]["refund_amount"] = 4
    mismatch_ack = client.post(url, headers=device_headers, json={"events": [mismatched_amount]}).json()["events"][0]
    assert mismatch_ack["status"] == "conflict"
    assert mismatch_ack["code"] == "offline_return_amount_conflict"

    second_return = return_event()
    assert client.post(url, headers=device_headers, json={"events": [second_return]}).json()["events"][0]["status"] == "applied"
    excess = return_event()
    excess_ack = client.post(url, headers=device_headers, json={"events": [excess]}).json()["events"][0]
    assert excess_ack["status"] == "conflict"
    assert excess_ack["code"] == "offline_return_quantity_conflict"

    with SessionLocal() as db:
        set_tenant_context(db, tenant["tenant_id"])
        sale = db.scalar(select(Sale).where(Sale.transaction_id == sale_transaction_id))
        item = db.get(SaleItem, line_id)
        customer_record = db.get(Customer, UUID(customer["id"]))
        returns = db.scalars(select(SaleReturn).where(SaleReturn.sale_id == sale.id)).all()
        movements = db.scalars(select(StockMovement).where(
            StockMovement.transaction_id.in_([UUID(first_return["transaction_id"]), UUID(second_return["transaction_id"])]),
        )).all()
        assert sale.status == "refunded" and sale.amount_paid == Decimal("10.00")
        assert item.returned_qty == 2
        assert customer_record.credit_balance == Decimal("0.00")
        assert len(returns) == 2 and all(row.cash_refund == Decimal("0.00") for row in returns)
        assert len(movements) == 2 and all(row.quantity_delta == 1 for row in movements)
    refreshed_batch = client.get(
        f"/api/v1/tenants/{tenant['tenant_id']}/shops/{tenant['shop_id']}/batches", headers=user_headers,
    ).json()[0]
    assert refreshed_batch["quantity"] == 4


def test_migration_downgrade_and_recovery(client):
    from app.db.session import engine

    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    with engine.connect() as connection:
        tables = set(connection.exec_driver_sql("SELECT tablename FROM pg_tables WHERE schemaname='public'").scalars())
    assert {"tenants", "shops", "medicine_batches", "stock_movements", "sync_events", "sale_returns"}.issubset(tables)
    with engine.connect() as connection:
        role = connection.exec_driver_sql("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user").one()
        assert role == (False, False)
        rls = connection.exec_driver_sql("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = 'sales'").one()
        assert rls == (True, True)
        return_rls = connection.exec_driver_sql("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = 'sale_returns'").one()
        assert return_rls == (True, True)
