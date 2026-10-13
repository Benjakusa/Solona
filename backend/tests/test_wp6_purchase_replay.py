from datetime import datetime, timezone
from uuid import uuid4

from tests.test_api_integration import create_inventory, register


def test_wp6_float_purchase_event_replays_without_decimal_type_error(client):
    tenant, user_headers = register(client, "wp6-purchase-float")
    workspace = client.get("/api/v1/auth/workspaces", headers=user_headers).json()
    device = client.post(
        f"/api/v1/tenants/{tenant['tenant_id']}/devices", headers=user_headers,
        json={"shop_id": tenant["shop_id"], "name": "Purchase terminal", "platform": "test"},
    ).json()
    medicine, supplier = create_inventory(
        client, tenant["tenant_id"], user_headers, tenant["shop_id"], quantity=10,
    )
    event_id, transaction_id, cloud_batch_id = uuid4(), uuid4(), uuid4()
    event = {
        "event_id": str(event_id), "transaction_id": str(transaction_id),
        "tenant_id": tenant["tenant_id"], "shop_id": tenant["shop_id"],
        "device_id": device["id"], "event_type": "purchase.received",
        "aggregate_id": "purchase-float", "schema_version": 1,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "payload": {"cashier_cloud_id": workspace["user_id"],
                    "cloud_supplier_id": supplier["id"], "cloud_medicine_id": medicine["id"],
                    "cloud_batch_id": str(cloud_batch_id), "batch_no": "NEW-BATCH",
                    "expiry_date": "2030-01-01", "quantity": 10,
                    "purchase_cost": 2.5, "selling_price": 5.0,
                    "total_cost": 25.0, "amount_paid": 25.0},
    }
    device_headers = {"X-Device-ID": device["id"], "X-Device-Secret": device["device_credential"]}
    url = f"/api/v1/tenants/{tenant['tenant_id']}/shops/{tenant['shop_id']}/sync/events"

    result = client.post(url, headers=device_headers, json={"events": [event]})

    assert result.status_code == 200, result.text
    ack = result.json()["events"][0]
    assert ack["status"] == "applied"


def test_wp6_medicine_update_movement_changes_cloud_batch_quantity(client):
    tenant, user_headers = register(client, "wp6-medicine-movement")
    workspace = client.get("/api/v1/auth/workspaces", headers=user_headers).json()
    device = client.post(
        f"/api/v1/tenants/{tenant['tenant_id']}/devices", headers=user_headers,
        json={"shop_id": tenant["shop_id"], "name": "Stock terminal", "platform": "test"},
    ).json()
    medicine, _ = create_inventory(client, tenant["tenant_id"], user_headers, tenant["shop_id"], quantity=10)
    batch = client.get(
        f"/api/v1/tenants/{tenant['tenant_id']}/shops/{tenant['shop_id']}/batches", headers=user_headers,
    ).json()[0]
    event = {
        "event_id": str(uuid4()), "transaction_id": str(uuid4()),
        "tenant_id": tenant["tenant_id"], "shop_id": tenant["shop_id"],
        "device_id": device["id"], "event_type": "medicine.updated",
        "aggregate_id": medicine["id"], "schema_version": 1,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "payload": {"cashier_cloud_id": workspace["user_id"],
                    "cloud_medicine_id": medicine["id"], "name": "Paracetamol",
                    "movements": [{"batch_id": batch["id"], "cloud_batch_id": batch["id"], "delta": -7}]},
    }
    device_headers = {"X-Device-ID": device["id"], "X-Device-Secret": device["device_credential"]}
    url = f"/api/v1/tenants/{tenant['tenant_id']}/shops/{tenant['shop_id']}/sync/events"

    ack = client.post(url, headers=device_headers, json={"events": [event]}).json()["events"][0]
    current = client.get(
        f"/api/v1/tenants/{tenant['tenant_id']}/shops/{tenant['shop_id']}/batches", headers=user_headers,
    ).json()[0]

    assert ack["status"] == "applied"
    assert current["quantity"] == 3


def test_wp6_offline_sale_preserves_original_sale_timestamp(client):
    from datetime import timedelta
    from sqlalchemy import select
    from app.db.session import SessionLocal, set_tenant_context
    from app.models import Sale

    tenant, user_headers = register(client, "wp6-sale-time")
    workspace = client.get("/api/v1/auth/workspaces", headers=user_headers).json()
    device = client.post(
        f"/api/v1/tenants/{tenant['tenant_id']}/devices", headers=user_headers,
        json={"shop_id": tenant["shop_id"], "name": "Offline terminal", "platform": "test"},
    ).json()
    medicine, _ = create_inventory(client, tenant["tenant_id"], user_headers, tenant["shop_id"], quantity=10)
    batch = client.get(
        f"/api/v1/tenants/{tenant['tenant_id']}/shops/{tenant['shop_id']}/batches", headers=user_headers,
    ).json()[0]
    sold_at = datetime.now(timezone.utc) - timedelta(days=5)
    sale_transaction = uuid4()
    event = {
        "event_id": str(uuid4()), "transaction_id": str(sale_transaction),
        "tenant_id": tenant["tenant_id"], "shop_id": tenant["shop_id"],
        "device_id": device["id"], "event_type": "sale.created",
        "aggregate_id": "offline-sale", "schema_version": 1,
        "occurred_at": sold_at.isoformat(),
        "payload": {"invoice_no": "WP6-TIME-1", "cashier_cloud_id": workspace["user_id"],
                    "total_amount": 10, "discount": 0, "tax": 0,
                    "amount_paid": 10, "credit_amount": 0, "payment_id": str(uuid4()),
                    "payment_method": "cash",
                    "lines": [{"cloud_medicine_id": medicine["id"], "cloud_batch_id": batch["id"],
                               "cloud_sale_item_id": str(uuid4()), "quantity": 2,
                               "unit_price": 5, "unit_cost": 3.25, "subtotal": 10}]},
    }
    device_headers = {"X-Device-ID": device["id"], "X-Device-Secret": device["device_credential"]}
    url = f"/api/v1/tenants/{tenant['tenant_id']}/shops/{tenant['shop_id']}/sync/events"
    ack = client.post(url, headers=device_headers, json={"events": [event]}).json()["events"][0]

    assert ack["status"] == "applied"
    with SessionLocal() as db:
        set_tenant_context(db, tenant["tenant_id"])
        sale = db.scalar(select(Sale).where(Sale.transaction_id == sale_transaction))
        assert sale.sold_at.date() == sold_at.date()
