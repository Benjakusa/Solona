import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
from uuid import uuid4

from database import db_manager
from network.shop_server import ShopCoordinator, make_handler


def seed_database(path):
    source = sqlite3.connect(path)
    source.executescript("""
        CREATE TABLE medicine_batches(id TEXT,medicine_id INTEGER,batch_no TEXT,quantity INTEGER,expiry_date TEXT);
        CREATE TABLE sync_outbox(event_id TEXT,transaction_id TEXT,event_type TEXT,payload TEXT,occurred_at TEXT,status TEXT,acknowledged_at TEXT,last_error TEXT);
        CREATE TABLE sync_identity(singleton INTEGER,last_successful_sync TEXT,last_error TEXT);
    """)
    source.execute("INSERT INTO medicine_batches VALUES('batch-1',10,'LOT-1',5,'2030-01-01')")
    source.execute("INSERT INTO sync_identity VALUES(1,NULL,NULL)")
    source.commit()
    source.close()


def make_sale_event(batch_id="batch-1", quantity=4):
    return {"event_id": str(uuid4()), "transaction_id": str(uuid4()),
            "event_type": "sale.created", "occurred_at": "2026-10-12T10:00:00Z",
            "device_id": str(uuid4()), "payload": {"invoice_no": str(uuid4()), "lines": [
                {"batch_id": batch_id, "quantity": quantity},
            ]}}


def test_shop_coordinator_serializes_sales_and_deduplicates_retries(tmp_path):
    source = tmp_path / "opening.db"
    seed_database(source)
    coordinator = ShopCoordinator(tmp_path / "coordinator.db")
    coordinator.seed_from_local(source)
    events = [make_sale_event(quantity=4), make_sale_event(quantity=4)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(coordinator.process_event, events))
    assert sorted(result["status"] for result in results) == ["accepted", "conflict"]
    accepted_event = events[results.index(next(r for r in results if r["status"] == "accepted"))]
    duplicate = coordinator.process_event(accepted_event)
    assert duplicate["status"] == "accepted" and duplicate["duplicate"] is True
    assert coordinator.batch_snapshot()[0]["quantity"] == 1
    assert coordinator.pull()["cursor"] == 1
    db = coordinator.connect()
    assert len(db.execute("SELECT * FROM conflicts").fetchall()) == 1
    db.close()


def test_lan_requests_require_enrolled_terminal_signature(tmp_path):
    coordinator = ShopCoordinator(tmp_path / "coordinator.db")
    device_id = str(uuid4())
    credential = coordinator.enroll_device(device_id, "Test terminal")
    timestamp = 1_800_000_000
    path = "/changes?cursor=0"
    body = b""
    key = hashlib.sha256(credential.encode()).digest()
    signature = hmac.new(key, str(timestamp).encode() + b"." + path.encode() + b"." + body, hashlib.sha256).hexdigest()
    assert coordinator.verify_device_request(device_id, path, body, timestamp, signature, now=timestamp)
    assert not coordinator.verify_device_request(device_id, path, body, timestamp, "wrong", now=timestamp)
    assert not coordinator.verify_device_request(str(uuid4()), path, body, timestamp, signature, now=timestamp)
    with coordinator.connect() as db:
        db.execute("UPDATE devices SET revoked_at='2026-10-12T12:00:00Z' WHERE device_id=?", (device_id,))
    assert not coordinator.verify_device_request(device_id, path, body, timestamp, signature, now=timestamp)


def test_revoked_device_cannot_be_reenrolled_implicitly(tmp_path):
    coordinator = ShopCoordinator(tmp_path / "coordinator.db")
    device_id = str(uuid4())
    credential = coordinator.enroll_device(device_id, "Test terminal")
    assert coordinator.revoke_device(device_id)
    assert not coordinator.verify_device_request(device_id, "/batches", b"", 1_800_000_000,
                                                "unused", now=1_800_000_000)
    assert not coordinator.revoke_device(device_id)


def test_wp7_revoked_device_cannot_reenroll_with_shared_secret(tmp_path):
    import pytest

    coordinator = ShopCoordinator(tmp_path / "coordinator.db")
    device_id = str(uuid4())
    coordinator.enroll_device(device_id, "Test terminal")
    assert coordinator.revoke_device(device_id)

    with pytest.raises(PermissionError):
        coordinator.enroll_device(device_id, "Test terminal")


def test_wp7_invalid_content_length_is_rejected_without_reading_body(tmp_path):
    import socket
    import threading
    from http.server import ThreadingHTTPServer

    coordinator = ShopCoordinator(tmp_path / "coordinator.db")
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(coordinator, "test-enrollment-secret"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with socket.create_connection(server.server_address, timeout=2) as client:
            client.settimeout(2)
            client.sendall(b"POST /events HTTP/1.1\r\nHost: localhost\r\nContent-Length: -1\r\n\r\n")
            response = client.recv(4096)
        assert b"400 Bad Request" in response
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_wp7_database_does_not_store_device_hmac_key_in_plaintext(tmp_path):
    coordinator = ShopCoordinator(tmp_path / "coordinator.db")
    device_id = str(uuid4())
    credential = coordinator.enroll_device(device_id, "Encrypted key terminal")
    with coordinator.connect() as db:
        stored = db.execute("SELECT credential_hash FROM devices WHERE device_id=?", (device_id,)).fetchone()[0]

    assert stored.startswith("enc:")
    assert stored != hashlib.sha256(credential.encode()).hexdigest()
