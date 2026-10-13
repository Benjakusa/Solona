import json
import threading
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from database import db_manager
from logic import auth, inventory, sales, suppliers
from logic.sales import Cart
from logic import backup
from logic.sync import (
    acknowledge, apply_downloaded_change, cache_offline_authorization, due_events,
    offline_authorization_is_valid, pending_count, record_delivery_failure,
    apply_cloud_bootstrap, _apply_cloud_change,
)


@pytest.fixture
def local_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_manager, "DB_PATH", tmp_path / "pharmacy.db")
    monkeypatch.setattr(db_manager, "BACKUP_DIR", tmp_path / "backups")
    db_manager.init_db()
    yield


def make_medicine(quantity=8):
    return inventory.add_medicine(
        "Paracetamol", "Acetaminophen", "Pain relief", "LOT-OPEN", "2099-12-31",
        quantity, 3.25, 5.0, None,
    )


def test_sale_and_outbox_commit_atomically_and_survive_reopen(local_db):
    medicine = make_medicine(5)
    cart = Cart()
    cart.add(medicine, 2)
    sale = sales.checkout(cart, cashier_id=1)

    conn = db_manager.get_connection()
    assert conn.execute("SELECT quantity FROM medicines WHERE id=?", (medicine.id,)).fetchone()[0] == 3
    assert conn.execute("SELECT SUM(quantity) FROM medicine_batches WHERE medicine_id=?", (medicine.id,)).fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM local_payments WHERE sale_id=?", (sale["sale_id"],)).fetchone()[0] == 1
    assert pending_count(conn) == 2  # medicine creation and sale
    sale_event = conn.execute("SELECT event_id,payload FROM sync_outbox WHERE event_type='sale.created'").fetchone()
    event = json.loads(sale_event["payload"])
    assert event["transaction_id"] == conn.execute("SELECT transaction_id FROM sales WHERE id=?", (sale["sale_id"],)).fetchone()[0]
    assert event["payload"]["lines"][0]["unit_cost"] == 3.25
    conn.close()

    reopened = db_manager.get_connection()
    assert pending_count(reopened) == 2
    assert reopened.execute("SELECT status FROM sync_outbox").fetchone()[0] == "pending"
    reopened.close()


def test_outbox_failure_rolls_back_sale_and_stock(local_db, monkeypatch):
    medicine = make_medicine(2)
    cart = Cart()
    cart.add(medicine, 1)
    monkeypatch.setattr(sales, "enqueue_event", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
    with pytest.raises(RuntimeError, match="disk full"):
        sales.checkout(cart, cashier_id=1)
    conn = db_manager.get_connection()
    assert conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0] == 0
    assert conn.execute("SELECT quantity FROM medicines WHERE id=?", (medicine.id,)).fetchone()[0] == 2
    assert pending_count(conn) == 1  # the earlier medicine-created event remains queued
    conn.close()


def test_duplicate_ack_is_safe_and_failures_back_off(local_db):
    medicine = make_medicine(2)
    cart = Cart()
    cart.add(medicine, 1)
    sales.checkout(cart, cashier_id=1)
    conn = db_manager.get_connection()
    event_id = conn.execute("SELECT event_id FROM sync_outbox WHERE event_type='sale.created'").fetchone()[0]
    record_delivery_failure(conn, event_id, "timeout")
    conn.commit()
    row = conn.execute("SELECT attempts,next_attempt_at,last_error FROM sync_outbox WHERE event_id=?", (event_id,)).fetchone()
    assert row["attempts"] == 1 and row["last_error"] == "timeout"
    acknowledge(conn, [event_id, event_id])
    conn.commit()
    assert conn.execute("SELECT status FROM sync_outbox WHERE event_type='medicine.created'").fetchone()[0] == "pending"
    assert conn.execute("SELECT status FROM sync_outbox WHERE event_id=?", (event_id,)).fetchone()[0] == "acked"
    conn.close()


def test_expired_offline_authorization_is_denied_and_scope_is_fixed(local_db):
    conn = db_manager.get_connection()
    now = datetime.now(timezone.utc)
    cache_offline_authorization(
        conn, username="staff@example.test", user_uuid="server-user-id", full_name="Staff",
        role="cashier", password_hash="salt$digest", tenant_id="tenant-a", tenant_name="Tenant A",
        shop_id="shop-a", shop_name="Shop A", permissions=["sales:write"], now=now,
    )
    conn.commit()
    cached = conn.execute("SELECT * FROM offline_auth_cache").fetchone()
    assert offline_authorization_is_valid(cached, now + timedelta(hours=71))
    assert not offline_authorization_is_valid(cached, now + timedelta(hours=73))
    conn.close()


def test_catalogue_download_is_idempotent_and_flags_local_pending_edit(local_db):
    medicine = make_medicine(1)
    conn = db_manager.get_connection()
    from logic.sync import enqueue_event
    enqueue_event(conn, "medicine.updated", str(medicine.id), {"name": "local edit"})
    change = {"id": str(medicine.id), "local_id": medicine.id, "name": "Remote name"}
    assert apply_downloaded_change(conn, change_id="change-1", stream="catalogue", version="10",
                                   change_type="medicine.update", payload=change)
    assert not apply_downloaded_change(conn, change_id="change-1", stream="catalogue", version="10",
                                       change_type="medicine.update", payload=change)
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM sync_conflicts WHERE status='open'").fetchone()[0] == 1
    assert conn.execute("SELECT name FROM medicines WHERE id=?", (medicine.id,)).fetchone()[0] == "Paracetamol"
    conn.close()


def test_simultaneous_sales_from_one_local_database_do_not_oversell(local_db):
    medicine = make_medicine(1)
    results = []
    barrier = threading.Barrier(2)

    def sell():
        cart = Cart()
        cart.add(inventory.get_medicine(medicine.id), 1)
        barrier.wait()
        try:
            sales.checkout(cart, cashier_id=1)
            results.append("sold")
        except ValueError:
            results.append("rejected")

    workers = [threading.Thread(target=sell) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert sorted(results) == ["rejected", "sold"]
    conn = db_manager.get_connection()
    assert conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0] == 1
    assert conn.execute("SELECT quantity FROM medicines WHERE id=?", (medicine.id,)).fetchone()[0] == 0
    conn.close()


def test_fefo_batch_deductions_purchases_and_returns_are_auditable(local_db):
    conn = db_manager.get_connection()
    conn.execute("UPDATE users SET cloud_uuid=? WHERE id=1", (str(uuid4()),))
    conn.commit()
    conn.close()
    medicine = make_medicine(5)
    suppliers.record_purchase_order(medicine.id, 2, "EARLIER-LOT", "2030-01-01", 4.0, 8.0)
    cart = Cart()
    cart.add(inventory.get_medicine(medicine.id), 6)
    sale = sales.checkout(cart, cashier_id=1)
    conn = db_manager.get_connection()
    lines = conn.execute(
        "SELECT mb.batch_no,si.quantity,si.unit_price,si.unit_cost,si.cloud_uuid "
        "FROM sale_items si JOIN medicine_batches mb ON mb.id=si.batch_id WHERE si.sale_id=? ORDER BY si.id",
        (sale["sale_id"],),
    ).fetchall()
    assert [(row["batch_no"], row["quantity"]) for row in lines] == [("EARLIER-LOT", 2), ("LOT-OPEN", 4)]
    assert [row["unit_cost"] for row in lines] == [4.0, 3.25]
    assert conn.execute("SELECT COUNT(*) FROM local_purchases").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM stock_movements WHERE movement_type='sale'").fetchone()[0] == 2
    conn.close()

    conn = db_manager.get_connection()
    first_line = conn.execute("SELECT id,batch_id,cloud_uuid FROM sale_items WHERE sale_id=? ORDER BY id LIMIT 1", (sale["sale_id"],)).fetchone()
    conn.close()
    returned = sales.process_return(first_line["id"], 1, "Damaged pack", 1)
    assert returned["refund_amount"] == 5.0
    conn = db_manager.get_connection()
    assert conn.execute("SELECT quantity FROM medicine_batches WHERE id=?", (first_line["batch_id"],)).fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM sale_returns WHERE sale_id=?", (sale["sale_id"],)).fetchone()[0] == 1
    return_event = json.loads(conn.execute("SELECT payload FROM sync_outbox WHERE event_type='sale.returned'").fetchone()[0])
    return_payload = return_event["payload"]
    assert return_payload["sale_transaction_id"] == conn.execute("SELECT transaction_id FROM sales WHERE id=?", (sale["sale_id"],)).fetchone()[0]
    assert return_payload["cloud_sale_item_id"] == first_line["cloud_uuid"]
    assert return_payload["cloud_batch_id"]
    assert return_payload["cashier_cloud_id"]
    conn.close()


def test_legacy_sqlite_migration_preserves_stock_as_opening_batch(tmp_path, monkeypatch):
    monkeypatch.setattr(db_manager, "DB_PATH", tmp_path / "legacy.db")
    raw = db_manager.get_connection()
    raw.executescript(db_manager.SCHEMA_PATH.read_text(encoding="utf-8"))
    raw.execute("INSERT INTO medicines(name,batch_no,expiry_date,quantity,purchase_price,sale_price) VALUES('Legacy item','OLD-LOT','2031-05-01',7,2.5,4.0)")
    raw.commit()
    raw.close()
    db_manager.init_db()
    conn = db_manager.get_connection()
    batch = conn.execute("SELECT batch_no,expiry_date,quantity,purchase_price,sale_price,source FROM medicine_batches").fetchone()
    assert tuple(batch) == ("OLD-LOT", "2031-05-01", 7, 2.5, 4.0, "opening")
    assert conn.execute("SELECT SUM(quantity_delta) FROM stock_movements WHERE movement_type='opening'").fetchone()[0] == 7
    conn.close()


def test_first_run_has_no_shared_password_and_recovery_requires_owner_pin(local_db):
    admin = auth.login("admin", "admin123")
    assert admin is None
    assert not auth.verify_recovery_pin("anything")
    with pytest.raises(ValueError):
        auth.recover_admin_access("", "too-short")
    auth.set_recovery_pin("owner-recovery-passphrase-9371")
    assert not auth.verify_recovery_pin("wrong-passphrase")
    assert auth.verify_recovery_pin("owner-recovery-passphrase-9371")
    with pytest.raises(ValueError, match="Recovery PIN"):
        auth.recover_admin_access("wrong-passphrase", "new-strong-password-2026")
    assert auth.recover_admin_access("owner-recovery-passphrase-9371", "new-strong-password-2026") == "admin"
    assert auth.login("admin", "new-strong-password-2026") is not None


def test_sqlite_online_backup_and_restore_include_a_consistent_snapshot(local_db, tmp_path, monkeypatch):
    medicine = make_medicine(4)
    monkeypatch.setattr(backup, "DB_PATH", db_manager.DB_PATH)
    monkeypatch.setattr(backup, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(backup, "DATA_DIR", tmp_path / "data")
    record = backup.create_backup()
    conn = db_manager.get_connection()
    conn.execute("UPDATE medicines SET quantity=1 WHERE id=?", (medicine.id,))
    conn.commit()
    conn.close()
    backup.restore_backup(record.filename)
    conn = db_manager.get_connection()
    assert conn.execute("SELECT quantity FROM medicines WHERE id=?", (medicine.id,)).fetchone()[0] == 4
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()


def test_cloud_bootstrap_and_remote_sale_application_are_idempotent(local_db):
    from uuid import uuid4

    tenant_id, shop_id = str(uuid4()), str(uuid4())
    medicine_id, batch_id = str(uuid4()), str(uuid4())
    snapshot = {
        "tenant_id": tenant_id, "shop_id": shop_id,
        "medicines": [{"id": medicine_id, "sku": "REMOTE-1", "name": "Remote item",
                        "generic_name": None, "manufacturer": None, "barcode": None,
                        "category": "General", "base_unit": "unit", "is_controlled": False}],
        "batches": [{"id": batch_id, "medicine_id": medicine_id, "batch_no": "R-LOT",
                     "expiry_date": "2035-01-01", "quantity": 5, "purchase_cost": "2.00",
                     "selling_price": "4.00", "supplier_id": None}],
        "suppliers": [], "customers": [],
    }
    apply_cloud_bootstrap(snapshot)
    apply_cloud_bootstrap(snapshot)
    conn = db_manager.get_connection()
    medicine = conn.execute("SELECT id,quantity FROM medicines WHERE cloud_uuid=?", (medicine_id,)).fetchone()
    batch = conn.execute("SELECT id,quantity FROM medicine_batches WHERE cloud_uuid=?", (batch_id,)).fetchone()
    assert medicine["quantity"] == 5 and batch["quantity"] == 5
    cashier_id = conn.execute("SELECT id FROM users WHERE username='admin'").fetchone()[0]
    cashier_uuid = str(uuid4())
    conn.execute("UPDATE users SET cloud_uuid=? WHERE id=?", (cashier_uuid, cashier_id))
    conn.commit()
    conn.close()

    event_id, transaction_id, item_id = str(uuid4()), str(uuid4()), str(uuid4())
    domain = {"shop_id": shop_id, "occurred_at": "2026-10-12T10:30:00Z",
              "invoice_no": "REMOTE-INV-1", "cashier_cloud_id": cashier_uuid,
              "cashier_name": "Remote cashier", "total_amount": 8, "discount": 0, "tax": 0,
              "amount_paid": 8, "payment_method": "cash", "payment_id": str(uuid4()),
              "lines": [{"cloud_medicine_id": medicine_id, "cloud_batch_id": batch_id,
                         "cloud_sale_item_id": item_id, "quantity": 2, "unit_price": 4,
                         "unit_cost": 2, "subtotal": 8}]}
    change = {"event_id": event_id, "transaction_id": transaction_id,
              "event_type": "sale.created", "cursor": 4,
              "payload": {"event_id": event_id, "transaction_id": transaction_id,
                          "event_type": "sale.created", "payload": domain}}
    conn = db_manager.get_connection()
    _apply_cloud_change(conn, change)
    _apply_cloud_change(conn, change)
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM sales WHERE transaction_id=?", (transaction_id,)).fetchone()[0] == 1
    assert conn.execute("SELECT quantity FROM medicine_batches WHERE cloud_uuid=?", (batch_id,)).fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM local_payments WHERE sale_id=(SELECT id FROM sales WHERE transaction_id=?)",
                        (transaction_id,)).fetchone()[0] == 1
    conn.close()


def test_online_cloud_user_cache_obeys_expiry(local_db):
    conn = db_manager.get_connection()
    conn.close()
    user = auth.cache_cloud_user_for_offline(
        username="owner@example.test", cloud_user_id="cloud-1", full_name="Owner", role="owner",
        tenant_id="tenant-1", tenant_name="Tenant", shop_id="shop-1", shop_name="Shop",
        permissions=["staff:manage", "tenant:manage"], password="local-cache-password",
    )
    assert user.is_admin
    assert auth.login("owner@example.test", "local-cache-password") == user
    conn = db_manager.get_connection()
    conn.execute("UPDATE offline_auth_cache SET authorized_until='2000-01-01T00:00:00Z'")
    conn.commit()
    conn.close()
    assert auth.login("owner@example.test", "local-cache-password") is None


def test_wp3_refund_respects_sale_discount_tax_and_partial_returns(local_db):
    medicine = make_medicine(10)
    cart = Cart()
    cart.add(medicine, 10)
    cart.discount = 25.0
    cart.tax_percent = 20.0
    sale = sales.checkout(cart, cashier_id=1, payment_method="cash")
    conn = db_manager.get_connection()
    line_id = conn.execute("SELECT id FROM sale_items WHERE sale_id=?", (sale["sale_id"],)).fetchone()[0]
    conn.close()

    first = sales.process_return(line_id, 5, "Partial return", 1)
    second = sales.process_return(line_id, 5, "Remaining return", 1)

    assert first["refund_amount"] == 15.0
    assert second["refund_amount"] == 15.0
    assert first["refund_amount"] + second["refund_amount"] == sale["total"]


def test_wp4_expiry_alert_uses_each_in_stock_batch(local_db):
    from datetime import date, timedelta

    soon = (date.today() + timedelta(days=5)).isoformat()
    later = (date.today() + timedelta(days=300)).isoformat()
    medicine = inventory.add_medicine(
        "Short dated medicine", "", "Test", "EXP-SOON", soon,
        10, 2.0, 5.0, None,
    )
    suppliers.record_purchase_order(medicine.id, 50, "EXP-LATER", later, 2.0)

    alerts = inventory.expiring_medicines(30)

    assert any(row.id == medicine.id and row.expiry_date == soon for row in alerts)


def test_wp8_backup_retention_never_unlinks_a_path_from_database(local_db, tmp_path):
    victim = tmp_path / "keep.txt"
    victim.write_text("important")
    conn = db_manager.get_connection()
    conn.execute(
        "INSERT INTO backup_log(filename,size,type,date) VALUES(?,1,'auto','2000-01-01')",
        (str(victim),),
    )
    for index in range(15):
        conn.execute(
            "INSERT INTO backup_log(filename,size,type,date) VALUES(?,1,'auto',?)",
            (str(tmp_path / f"backup-{index}.db"), f"2001-01-{index + 1:02d}"),
        )
    conn.commit()
    conn.close()

    backup._enforce_auto_retention()

    assert victim.read_text() == "important"


def test_wp8_backup_delete_rejects_traversal_and_symlink(local_db, tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(backup, "DATA_DIR", tmp_path / "data")
    backup.BACKUP_DIR.mkdir(exist_ok=True)
    backup.DATA_DIR.mkdir(exist_ok=True)
    victim = tmp_path / "victim.db"
    victim.write_text("do not delete")
    link = backup.BACKUP_DIR / "linked.db"
    link.symlink_to(victim)
    conn = db_manager.get_connection()
    for name in (str(victim), str(link)):
        conn.execute("INSERT INTO backup_log(filename,size,type) VALUES(?,1,'manual')", (name,))
    conn.commit()
    ids = [row[0] for row in conn.execute("SELECT id FROM backup_log ORDER BY id")]
    conn.close()

    for record_id in ids:
        with pytest.raises(ValueError):
            backup.delete_backup(record_id)
    assert victim.read_text() == "do not delete"
    assert link.is_symlink()


def test_wp5_passwords_require_12_characters_and_bad_hash_is_a_normal_failure(local_db):
    from logic.security import verify_password

    with pytest.raises(ValueError):
        auth.create_user("short-pass-user", "abcd", "Short Password", "cashier")
    assert verify_password("anything", "not-a-valid-hash") is False
    assert verify_password("anything", "zz$zz") is False


def test_wp5_last_active_admin_cannot_be_demoted_or_deactivated(local_db):
    admin = auth.list_users()[0]

    with pytest.raises(ValueError, match="last active administrator"):
        auth.update_user(admin.id, admin.full_name, "cashier", False)

    conn = db_manager.get_connection()
    row = conn.execute("SELECT role,is_active FROM users WHERE id=?", (admin.id,)).fetchone()
    conn.close()
    assert row["role"] == "admin" and row["is_active"] == 1


def test_wp4_cart_rejects_expired_batches_before_checkout(local_db):
    from datetime import date, timedelta

    expired = (date.today() - timedelta(days=1)).isoformat()
    medicine = inventory.add_medicine("Expired lot", "", "Test", "EXPIRED", expired,
                                       0, 1.0, 2.0, None)
    conn = db_manager.get_connection()
    conn.execute("UPDATE medicine_batches SET quantity=3,expiry_date=? WHERE medicine_id=?",
                 (expired, medicine.id))
    conn.execute("UPDATE medicines SET quantity=3 WHERE id=?", (medicine.id,))
    conn.commit()
    conn.close()
    cart = Cart()

    with pytest.raises(ValueError, match="sellable"):
        cart.add(medicine, 1)


def test_wp6_cloud_bootstrap_sets_pos_price_from_first_cloud_batch(local_db):
    snapshot = {
        "tenant_id": "tenant-pricing", "shop_id": "shop-pricing",
        "medicines": [{"id": "11111111-1111-1111-1111-111111111111", "sku": "SKU-CLOUD",
                       "name": "Cloud-priced medicine", "generic_name": "", "is_controlled": False}],
        "batches": [{"id": "22222222-2222-2222-2222-222222222222",
                     "medicine_id": "11111111-1111-1111-1111-111111111111",
                     "batch_no": "CLOUD-LOT", "expiry_date": "2099-01-01", "quantity": 8,
                     "purchase_cost": "10.00", "selling_price": "25.00"}],
        "suppliers": [], "customers": [],
    }

    apply_cloud_bootstrap(snapshot)

    conn = db_manager.get_connection()
    price = conn.execute("SELECT sale_price FROM medicines WHERE cloud_uuid=?",
                         ("11111111-1111-1111-1111-111111111111",)).fetchone()[0]
    conn.close()
    assert price == 25.0


def test_wp3_profit_report_uses_historical_cost_discount_and_returns(local_db):
    from datetime import date
    from logic import reports

    medicine = make_medicine(4)
    cart = Cart()
    cart.add(medicine, 2)
    cart.discount = 2.0
    cart.tax_percent = 12.5
    sale = sales.checkout(cart, cashier_id=1, payment_method="cash")
    conn = db_manager.get_connection()
    item = conn.execute("SELECT id FROM sale_items WHERE sale_id=?", (sale["sale_id"],)).fetchone()
    conn.execute("UPDATE medicines SET purchase_price=9 WHERE id=?", (medicine.id,))
    conn.commit()
    conn.close()
    sales.process_return(item["id"], 1, "Partial", 1)

    report = reports.profit_loss_summary(date.today().isoformat(), date.today().isoformat())

    assert report["revenue"] == 4.5
    assert report["cost"] == 3.25
    assert report["profit"] == 1.25


def test_wp3_invoice_counter_recovers_after_deleted_invoice_gap(local_db):
    conn = db_manager.get_connection()
    identity = conn.execute("SELECT device_id FROM sync_identity WHERE singleton=1").fetchone()[0]
    from datetime import datetime
    today = datetime.now().strftime("%Y%m%d")
    terminal = identity.replace("-", "")[:6].upper()
    prefix = f"INV-{today}-{terminal}-"
    conn.execute(
        "INSERT INTO sales(invoice_no,cashier_id,total_amount) VALUES(?,?,0)",
        (prefix + "0001", 1),
    )
    conn.execute(
        "INSERT INTO sales(invoice_no,cashier_id,total_amount) VALUES(?,?,0)",
        (prefix + "0003", 1),
    )
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    generated = sales._generate_invoice_no(conn)
    conn.rollback()
    conn.close()

    assert generated == prefix + "0004"


def test_wp4_receipt_explicitly_updates_medicine_selling_price(local_db):
    medicine = make_medicine(3)

    suppliers.record_purchase_order(medicine.id, 2, "PRICE-LOT", "2099-12-31", 3.25,
                                    new_sale_price=9.99)

    assert inventory.get_medicine(medicine.id).sale_price == 9.99


def test_wp4_editor_cannot_change_stock_without_a_receipt(local_db):
    medicine = make_medicine(3)

    with pytest.raises(ValueError, match="stock adjustment"):
        inventory.update_medicine(
            medicine.id, medicine.name, medicine.generic_name, medicine.category,
            medicine.batch_no, medicine.expiry_date, 4, medicine.purchase_price,
            medicine.sale_price, medicine.supplier_id, medicine.low_stock_threshold,
            barcode=medicine.barcode, manufacturer=medicine.manufacturer,
            units_per_pack=medicine.units_per_pack,
        )


def test_wp4_positive_adjustment_requires_batch_expiry_cost_and_reason(local_db):
    medicine = make_medicine(3)

    with pytest.raises(ValueError, match="batch number, expiry date, unit cost, and reason"):
        inventory.adjust_stock(medicine.id, 2, actor_id=1)


def test_wp6_pulled_medicine_update_applies_stock_movement_once(local_db):
    medicine = make_medicine(10)
    conn = db_manager.get_connection()
    batch = conn.execute("SELECT id,cloud_uuid FROM medicine_batches WHERE medicine_id=?", (medicine.id,)).fetchone()
    conn.execute("UPDATE sync_outbox SET status='acked'")
    conn.commit()
    change = {
        "event_id": str(uuid4()), "cursor": 22, "event_type": "medicine.updated",
        "transaction_id": str(uuid4()),
        "payload": {"payload": {"payload": {
            "cloud_medicine_id": conn.execute(
                "SELECT cloud_uuid FROM medicines WHERE id=?", (medicine.id,)
            ).fetchone()[0],
            "name": "Paracetamol", "shop_id": "shop-1", "occurred_at": "2026-10-13T10:00:00Z",
            "movements": [{"cloud_batch_id": batch["cloud_uuid"], "delta": -3}],
        }}},
    }

    _apply_cloud_change(conn, change)
    conn.commit()
    assert conn.execute("SELECT quantity FROM medicine_batches WHERE id=?", (batch["id"],)).fetchone()[0] == 7
    _apply_cloud_change(conn, change)
    conn.commit()
    assert conn.execute("SELECT quantity FROM medicine_batches WHERE id=?", (batch["id"],)).fetchone()[0] == 7
    conn.close()
