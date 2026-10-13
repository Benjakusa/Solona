"""Durable local outbox and bounded offline authorization policy.

Events are inserted using the caller's SQLite transaction so a domain write
cannot commit without its corresponding synchronization record. Delivery is
at least once; the immutable event_id is the server idempotency key.
"""
import json
from typing import Optional, List, Dict
import hashlib
import hmac
import random
import sqlite3
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

OFFLINE_AUTH_HOURS = 72


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def enqueue_event(conn: sqlite3.Connection, event_type: str, aggregate_id: str,
                  payload: dict, transaction_id: Optional[str] = None) -> str:
    identity = conn.execute("SELECT * FROM sync_identity WHERE singleton=1").fetchone()
    if identity is None:
        raise RuntimeError("Local synchronization identity is not initialized.")
    event_id = str(uuid.uuid4())
    tx_id = transaction_id or str(uuid.uuid4())
    now = utc_now()
    envelope = {
        "event_id": event_id,
        "transaction_id": tx_id,
        "event_type": event_type,
        "aggregate_id": str(aggregate_id),
        "schema_version": 1,
        "tenant_id": identity["tenant_id"],
        "shop_id": identity["shop_id"],
        "device_id": identity["device_id"],
        "occurred_at": now,
        "payload": payload,
    }
    conn.execute(
        "INSERT INTO sync_outbox(event_id,transaction_id,tenant_id,shop_id,device_id,event_type,"
        "aggregate_id,occurred_at,payload,next_attempt_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (event_id, tx_id, identity["tenant_id"], identity["shop_id"], identity["device_id"],
         event_type, str(aggregate_id), now, json.dumps(envelope, separators=(",", ":")), now),
    )
    return event_id


def pending_count(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM sync_outbox WHERE status IN ('pending','failed','inflight')"
    ).fetchone()[0]


def due_events(conn: sqlite3.Connection, limit: int = 50) -> List[Dict]:
    rows = conn.execute(
        "SELECT event_id,payload,attempts FROM sync_outbox WHERE status IN ('pending','failed','inflight') "
        "AND next_attempt_at<=? ORDER BY occurred_at LIMIT ?", (utc_now(), limit)
    ).fetchall()
    return [dict(row) for row in rows]


def record_delivery_failure(conn: sqlite3.Connection, event_id: str, message: str) -> None:
    row = conn.execute("SELECT attempts FROM sync_outbox WHERE event_id=?", (event_id,)).fetchone()
    if row is None:
        return
    attempts = row["attempts"] + 1
    delay = min(3600, 2 ** min(attempts, 11)) * random.uniform(0.8, 1.2)
    next_at = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat().replace("+00:00", "Z")
    conn.execute(
        "UPDATE sync_outbox SET status='pending',attempts=?,next_attempt_at=?,last_error=? WHERE event_id=?",
        (attempts, next_at, message[:300], event_id),
    )


def acknowledge(conn: sqlite3.Connection, event_ids: List[str]) -> None:
    now = utc_now()
    conn.executemany(
        "UPDATE sync_outbox SET status='acked',acknowledged_at=?,last_error=NULL WHERE event_id=?",
        [(now, event_id) for event_id in event_ids],
    )


def cache_offline_authorization(conn: sqlite3.Connection, *, username: str, user_uuid: str,
                               full_name: str, role: str, password_hash: str,
                               tenant_id: str, tenant_name: str, shop_id: str, shop_name: str,
                               permissions: List[str], now: Optional[datetime] = None) -> None:
    """Cache a credential only after successful server authentication.

    The caller supplies a locally salted password hash, never a server hash.
    Offline access expires after 72 hours and an inactive server membership
    cannot be refreshed; revocation is bounded by this documented window.
    """
    now = now or datetime.now(timezone.utc)
    last_online = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    until = (now + timedelta(hours=OFFLINE_AUTH_HOURS)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    conn.execute(
        "INSERT INTO offline_auth_cache(username,user_uuid,full_name,role,password_hash,tenant_id,tenant_name,"
        "shop_id,shop_name,permissions,last_online_at,authorized_until,is_active) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1) "
        "ON CONFLICT(username) DO UPDATE SET user_uuid=excluded.user_uuid,full_name=excluded.full_name,"
        "role=excluded.role,password_hash=excluded.password_hash,tenant_id=excluded.tenant_id,"
        "tenant_name=excluded.tenant_name,shop_id=excluded.shop_id,shop_name=excluded.shop_name,"
        "permissions=excluded.permissions,last_online_at=excluded.last_online_at,"
        "authorized_until=excluded.authorized_until,is_active=1",
        (username, user_uuid, full_name, role, password_hash, tenant_id, tenant_name, shop_id, shop_name,
         json.dumps(sorted(set(permissions))), last_online, until),
    )


def offline_authorization_is_valid(row, now: Optional[datetime] = None) -> bool:
    if row is None or not row["is_active"]:
        return False
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    expiry = datetime.fromisoformat(row["authorized_until"].replace("Z", "+00:00"))
    return now < expiry.astimezone(timezone.utc)


def apply_downloaded_change(conn: sqlite3.Connection, *, change_id: str, stream: str,
                            version: str, change_type: str, payload: dict) -> bool:
    """Store remote changes idempotently; flag local edit collisions for review."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO downloaded_changes(change_id,stream,version,change_type,payload,downloaded_at) "
        "VALUES(?,?,?,?,?,?)",
        (change_id, stream, version, change_type, json.dumps(payload), utc_now()),
    )
    if not cur.rowcount:
        return False
    aggregate_id = str(payload.get("id", payload.get("medicine_id", "")))
    if aggregate_id:
        pending = conn.execute(
            "SELECT event_id FROM sync_outbox WHERE aggregate_id=? AND status IN ('pending','failed','inflight') LIMIT 1",
            (aggregate_id,),
        ).fetchone()
        if pending:
            conn.execute(
                "INSERT INTO sync_conflicts(conflict_id,event_id,conflict_type,details,created_at) VALUES(?,?,?,?,?)",
                (str(uuid.uuid4()), pending["event_id"], "catalogue_changed_remotely",
                 json.dumps({"change_id": change_id, "payload": payload}), utc_now()),
            )
            return True
    # Catalogue changes are applied only for recognized, non-stock fields.
    local_id = payload.get("local_id", payload.get("medicine_id"))
    if change_type in {"medicine.update", "medicine.updated"} and local_id:
        allowed = {k: payload[k] for k in ("name", "generic_name", "manufacturer", "category", "sale_price", "is_active") if k in payload}
        if allowed:
            conn.execute(
                "UPDATE medicines SET " + ",".join(f"{key}=?" for key in allowed) +
                ",updated_at=datetime('now','localtime') WHERE id=?",
                [*allowed.values(), local_id],
            )
    conn.execute("UPDATE downloaded_changes SET applied_at=? WHERE change_id=?", (utc_now(), change_id))
    conn.execute(
        "INSERT INTO sync_checkpoints(stream,cursor,updated_at) VALUES(?,?,?) "
        "ON CONFLICT(stream) DO UPDATE SET cursor=excluded.cursor,updated_at=excluded.updated_at",
        (stream, version, utc_now()),
    )
    return True


def configure_lan_coordinator(base_url: str, enrollment_secret: str, device_name: str = "This computer") -> str:
    """Enroll this installation and protect its per-device key in OS keyring."""
    import keyring
    from database.db_manager import get_connection

    base_url = base_url.rstrip("/")
    conn = get_connection()
    try:
        identity = conn.execute("SELECT * FROM sync_identity WHERE singleton=1").fetchone()
        device_id = identity["device_id"]
    finally:
        conn.close()
    body = json.dumps({"device_id": device_id, "name": device_name}, separators=(",", ":")).encode()
    request = urllib.request.Request(base_url + "/enroll", data=body, method="POST", headers={
        "Content-Type": "application/json", "X-Shani-Enrollment": enrollment_secret,
    })
    with urllib.request.urlopen(request, timeout=5) as response:
        result = json.loads(response.read())
    keyring.set_password("shani-pharmacy-lan", result["device_id"], result["device_credential"])
    conn = get_connection()
    try:
        conn.execute("UPDATE sync_identity SET device_id=?,device_name=?,coordination_mode='lan_client',lan_server_url=?,last_error=NULL WHERE singleton=1",
                     (result["device_id"], device_name, base_url))
        conn.commit()
    finally:
        conn.close()
    return result["device_id"]


def _lan_request(base_url: str, device_id: str, credential: str, path: str, body: bytes = b""):
    timestamp = str(int(time.time()))
    key = hashlib.sha256(credential.encode()).digest()
    signature = hmac.new(key, timestamp.encode() + b"." + path.encode() + b"." + body, hashlib.sha256).hexdigest()
    request = urllib.request.Request(base_url + path, data=body if body else None,
                                     method="POST" if body else "GET", headers={
        "X-Shani-Device": device_id, "X-Shani-Time": timestamp,
        "X-Shani-Signature": signature, "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def sync_lan_once() -> dict:
    """Push due events and pull serialized shop changes. Safe to call again."""
    import keyring
    from database.db_manager import get_connection

    conn = get_connection()
    try:
        identity = conn.execute("SELECT * FROM sync_identity WHERE singleton=1").fetchone()
        if identity["coordination_mode"] != "lan_client" or not identity["lan_server_url"]:
            return {"status": "not_configured"}
        credential = keyring.get_password("shani-pharmacy-lan", identity["device_id"])
        if not credential:
            raise RuntimeError("This terminal is not enrolled with the shop coordinator.")
        base = identity["lan_server_url"].rstrip("/")
        for event in due_events(conn, 30):
            payload = event["payload"].encode()
            status, result = _lan_request(base, identity["device_id"], credential, "/events", payload)
            if status == 200 and result.get("status") == "accepted":
                acknowledge(conn, [event["event_id"]])
                conn.commit()
            elif status == 409:
                conn.execute("UPDATE sync_outbox SET status='conflict',attempts=attempts+1,last_error=? WHERE event_id=?",
                             (result.get("code", "server_conflict"), event["event_id"]))
                conn.execute("INSERT INTO sync_conflicts(conflict_id,event_id,conflict_type,details,created_at) VALUES(?,?,?,?,?)",
                             (str(uuid.uuid4()), event["event_id"], result.get("code", "server_conflict"),
                              json.dumps(result), utc_now()))
                conn.commit()
            else:
                record_delivery_failure(conn, event["event_id"], result.get("detail", f"HTTP {status}"))
                conn.commit()
                break

        checkpoint = conn.execute("SELECT cursor FROM sync_checkpoints WHERE stream='lan' ").fetchone()
        cursor = int(checkpoint["cursor"]) if checkpoint and checkpoint["cursor"] else 0
        status, result = _lan_request(base, identity["device_id"], credential, f"/changes?cursor={cursor}")
        if status != 200:
            raise RuntimeError(result.get("detail", f"LAN coordinator returned HTTP {status}"))
        for change in result.get("changes", []):
            _apply_lan_change(conn, change, identity["device_id"])
        if result.get("cursor", cursor) != cursor:
            conn.execute("INSERT INTO sync_checkpoints(stream,cursor,updated_at) VALUES('lan',?,?) "
                         "ON CONFLICT(stream) DO UPDATE SET cursor=excluded.cursor,updated_at=excluded.updated_at",
                         (str(result["cursor"]), utc_now()))
        conn.execute("UPDATE sync_identity SET last_successful_sync=?,last_error=NULL WHERE singleton=1", (utc_now(),))
        conn.commit()
        return {"status": "ok", "cursor": result.get("cursor", cursor)}
    except Exception as exc:
        conn.rollback()
        conn.execute("UPDATE sync_identity SET last_error=? WHERE singleton=1", (str(exc)[:250],))
        conn.commit()
        return {"status": "error", "error": str(exc)[:250]}
    finally:
        conn.close()


def _apply_lan_change(conn, change: dict, device_id: str) -> None:
    canonical = change["payload"]
    event_id = change["event_id"]
    if not conn.execute("INSERT OR IGNORE INTO downloaded_changes(change_id,stream,version,change_type,payload,downloaded_at) VALUES(?,?,?,?,?,?)",
                        (event_id, "lan", str(change["cursor"]), change["event_type"], json.dumps(canonical), utc_now())).rowcount:
        return
    if conn.execute("SELECT 1 FROM sync_outbox WHERE event_id=?", (event_id,)).fetchone():
        conn.execute("UPDATE sync_outbox SET status='acked',acknowledged_at=?,last_error=NULL WHERE event_id=?",
                     (utc_now(), event_id))
        conn.execute("UPDATE downloaded_changes SET applied_at=? WHERE change_id=?", (utc_now(), event_id))
        return
    event_payload = canonical.get("payload", {})
    if change["event_type"] == "medicine.created":
        medicine_id = int(event_payload["medicine_id"])
        if not conn.execute("SELECT 1 FROM medicines WHERE id=?", (medicine_id,)).fetchone():
            conn.execute("INSERT INTO medicines(id,name,generic_name,manufacturer,category,barcode,batch_no,expiry_date,quantity,purchase_price,sale_price) "
                         "VALUES(?,?,?,?,?,?,?,?,0,?,?)",
                         (medicine_id, event_payload["name"], event_payload.get("generic_name"),
                          event_payload.get("manufacturer"), event_payload.get("category"),
                          event_payload.get("barcode"), event_payload.get("batch_no"),
                          event_payload.get("expiry_date"), event_payload.get("purchase_cost", 0),
                          event_payload.get("selling_price", 0)))
    local_pending = [json.loads(r[0]) for r in conn.execute("SELECT payload FROM sync_outbox WHERE status IN ('pending','failed','inflight')").fetchall()]
    movement_items = canonical.get("movements", [])
    for batch_id, delta in movement_items:
        batch_id, delta = str(batch_id), int(delta)
        medicine_id = event_payload.get("medicine_id")
        batch = conn.execute("SELECT medicine_id,quantity FROM medicine_batches WHERE id=?", (batch_id,)).fetchone()
        if batch is None and delta > 0 and medicine_id:
            conn.execute("INSERT INTO medicine_batches(id,medicine_id,batch_no,expiry_date,quantity,purchase_price,sale_price,source) "
                         "VALUES(?,?,?,?,0,?,?, 'remote')",
                         (batch_id, medicine_id, event_payload.get("batch_no", f"REMOTE-{batch_id[:8]}"),
                          event_payload.get("expiry_date"), event_payload.get("purchase_cost", 0),
                          event_payload.get("selling_price", 0)))
            batch = conn.execute("SELECT medicine_id,quantity FROM medicine_batches WHERE id=?", (batch_id,)).fetchone()
        if batch is None:
            _flag_remote_conflict(conn, event_id, "unknown_local_batch", {"batch_id": batch_id})
            continue
        if _pending_affects_batch(local_pending, batch_id):
            _flag_remote_conflict(conn, event_id, "simultaneous_offline_stock_change", {"batch_id": batch_id, "delta": delta})
            continue
        if batch["quantity"] + delta < 0:
            _flag_remote_conflict(conn, event_id, "stale_local_stock", {"batch_id": batch_id, "delta": delta})
            continue
        conn.execute("UPDATE medicine_batches SET quantity=quantity+? WHERE id=?", (delta, batch_id))
        conn.execute("UPDATE medicines SET quantity=quantity+? WHERE id=?", (delta, batch["medicine_id"]))
    if change["event_type"] in {"medicine.updated", "medicine.deactivated"}:
        apply_downloaded_change(conn, change_id=f"catalog:{event_id}", stream="catalogue",
                                version=str(change["cursor"]),
                                change_type="medicine.updated" if change["event_type"] == "medicine.updated" else "medicine.deactivated",
                                payload=event_payload)
    conn.execute("UPDATE downloaded_changes SET applied_at=? WHERE change_id=?", (utc_now(), event_id))


def _pending_affects_batch(events: List[Dict], batch_id: str) -> bool:
    for event in events:
        payload = event.get("payload", {})
        if any(str(line.get("batch_id")) == batch_id for line in payload.get("lines", [])):
            return True
        if any(str(movement.get("batch_id")) == batch_id for movement in payload.get("movements", [])):
            return True
        if str(payload.get("batch_id")) == batch_id:
            return True
    return False


def _flag_remote_conflict(conn, event_id: str, conflict_type: str, details: dict) -> None:
    conn.execute("INSERT INTO sync_conflicts(conflict_id,event_id,conflict_type,details,created_at) VALUES(?,?,?,?,?)",
                 (str(uuid.uuid4()), event_id, conflict_type, json.dumps(details), utc_now()))


def sync_cloud_once() -> dict:
    """At-least-once cloud delivery with server acknowledgments and feed pull."""
    import keyring
    from database.db_manager import get_connection

    conn = get_connection()
    try:
        identity = conn.execute("SELECT * FROM sync_identity WHERE singleton=1").fetchone()
        if identity["coordination_mode"] != "cloud_client" or not identity["cloud_api_url"]:
            return {"status": "not_configured"}
        device_secret = keyring.get_password("shani-pharmacy-cloud", identity["device_id"])
        if not device_secret:
            raise RuntimeError("The registered device credential is unavailable in the operating-system keyring.")
        from logic.cloud import request_json

        base = identity["cloud_api_url"].rstrip("/")
        for event in due_events(conn, 50):
            envelope = json.loads(event["payload"])
            path = f"/api/v1/tenants/{identity['tenant_id']}/shops/{identity['shop_id']}/sync/events"
            try:
                response = request_json(base, path, method="POST", payload={"events": [envelope]},
                                        device=(identity["device_id"], device_secret))
                result = response["events"][0]
                if result["status"] == "applied":
                    acknowledge(conn, [event["event_id"]])
                else:
                    code = result.get("code") or result["status"]
                    conn.execute("UPDATE sync_outbox SET status='conflict',attempts=attempts+1,last_error=? WHERE event_id=?",
                                 (code, event["event_id"]))
                    _flag_remote_conflict(conn, event["event_id"], code, result)
                conn.commit()
            except Exception as exc:
                record_delivery_failure(conn, event["event_id"], str(exc))
                conn.commit()
                break
        checkpoint = conn.execute("SELECT cursor FROM sync_checkpoints WHERE stream='cloud' ").fetchone()
        cursor = int(checkpoint["cursor"]) if checkpoint and checkpoint["cursor"] else 0
        path = (f"/api/v1/tenants/{identity['tenant_id']}/shops/{identity['shop_id']}"
                f"/sync/changes?cursor={cursor}&limit=100")
        response = request_json(base, path, device=(identity["device_id"], device_secret))
        for change in response.get("changes", []):
            _apply_cloud_change(conn, change)
        if response.get("cursor", cursor) != cursor:
            conn.execute("INSERT INTO sync_checkpoints(stream,cursor,updated_at) VALUES('cloud',?,?) "
                         "ON CONFLICT(stream) DO UPDATE SET cursor=excluded.cursor,updated_at=excluded.updated_at",
                         (str(response["cursor"]), utc_now()))
        conn.execute("UPDATE sync_identity SET last_successful_sync=?,last_error=NULL WHERE singleton=1", (utc_now(),))
        conn.commit()
        return {"status": "ok", "cursor": response.get("cursor", cursor)}
    except Exception as exc:
        conn.rollback()
        conn.execute("UPDATE sync_identity SET last_error=? WHERE singleton=1", (str(exc)[:250],))
        conn.commit()
        return {"status": "error", "error": str(exc)[:250]}
    finally:
        conn.close()


def _apply_cloud_change(conn: sqlite3.Connection, change: dict) -> None:
    event_id = change["event_id"]
    if not conn.execute("INSERT OR IGNORE INTO downloaded_changes(change_id,stream,version,change_type,payload,downloaded_at) VALUES(?,?,?,?,?,?)",
                        (event_id, "cloud", str(change["cursor"]), change["event_type"], json.dumps(change["payload"]), utc_now())).rowcount:
        return
    envelope = change["payload"]
    data = envelope.get("payload", {}).get("payload", envelope.get("payload", {}))
    event_type = change["event_type"]
    tx_id = change["transaction_id"]
    if event_type == "medicine.created":
        _import_remote_medicine(conn, data)
    elif event_type == "medicine.updated":
        local = conn.execute("SELECT id FROM medicines WHERE cloud_uuid=?", (data.get("cloud_medicine_id"),)).fetchone()
        if local and not conn.execute("SELECT 1 FROM sync_outbox WHERE aggregate_id=? AND status IN ('pending','failed','inflight')",
                                      (str(local["id"]),)).fetchone():
            fields = {k: data[k] for k in ("name", "generic_name", "manufacturer", "category", "barcode", "sale_price") if k in data}
            if fields:
                conn.execute("UPDATE medicines SET " + ",".join(f"{key}=?" for key in fields) + " WHERE id=?",
                             [*fields.values(), local["id"]])
                if "sale_price" in fields:
                    conn.execute("UPDATE medicine_batches SET sale_price=? WHERE medicine_id=? AND quantity>0",
                                 (fields["sale_price"], local["id"]))
    elif event_type == "sale.created":
        _import_remote_sale(conn, data, tx_id, event_id)
    elif event_type == "purchase.received":
        _import_remote_purchase(conn, data, tx_id, event_id)
    elif event_type in {"stock.adjusted", "medicine.updated"}:
        _apply_remote_movements(conn, data, tx_id, event_id)
    elif event_type == "medicine.deactivated":
        conn.execute("UPDATE medicines SET is_active=0 WHERE cloud_uuid=?", (data.get("cloud_medicine_id"),))
    conn.execute("UPDATE downloaded_changes SET applied_at=? WHERE change_id=?", (utc_now(), event_id))


def _import_remote_medicine(conn, data: dict) -> None:
    cloud_id = data.get("cloud_medicine_id")
    if not cloud_id or conn.execute("SELECT 1 FROM medicines WHERE cloud_uuid=?", (cloud_id,)).fetchone():
        return
    cur = conn.execute(
        "INSERT INTO medicines(name,generic_name,manufacturer,category,barcode,quantity,purchase_price,sale_price,cloud_uuid,cloud_sku) "
        "VALUES(?,?,?,?,?,0,?,?,?,?)",
        (data["name"], data.get("generic_name"), data.get("manufacturer"), data.get("category"),
         data.get("barcode"), data.get("purchase_cost", 0), data.get("selling_price", 0), cloud_id, data.get("sku")),
    )
    medicine_id = cur.lastrowid
    batch_id = data.get("cloud_batch_id")
    if batch_id:
        conn.execute(
            "INSERT OR IGNORE INTO medicine_batches(id,medicine_id,cloud_uuid,batch_no,expiry_date,quantity,purchase_price,sale_price,source) "
            "VALUES(?,?,?,?,?,?,?,?,'cloud')",
            (batch_id, medicine_id, batch_id, data.get("batch_no", f"CLOUD-{batch_id[:8]}"),
             data.get("expiry_date"), data.get("quantity", 0), data.get("purchase_cost", 0),
             data.get("selling_price", 0)),
        )
        conn.execute("UPDATE medicines SET quantity=? WHERE id=?", (data.get("quantity", 0), medicine_id))


def _import_remote_sale(conn: sqlite3.Connection, data: dict, transaction_id: str, event_id: str) -> None:
    if conn.execute("SELECT 1 FROM sales WHERE transaction_id=?", (transaction_id,)).fetchone():
        return
    user_uuid = data.get("cashier_cloud_id")
    user = conn.execute("SELECT id FROM users WHERE cloud_uuid=?", (user_uuid,)).fetchone() if user_uuid else None
    if user is None:
        username = f"cloud-{(user_uuid or event_id)[:8]}"
        from logic.security import hash_password
        cursor = conn.execute("INSERT INTO users(username,password_hash,full_name,role,is_active,cloud_uuid) VALUES(?,?,?,'cashier',1,?)",
                              (username, hash_password(uuid.uuid4().hex), data.get("cashier_name", "Cloud staff"), user_uuid))
        cashier_id = cursor.lastrowid
    else:
        cashier_id = user["id"]
    customer = conn.execute("SELECT id FROM customers WHERE cloud_uuid=?", (data.get("customer_cloud_id"),)).fetchone() if data.get("customer_cloud_id") else None
    lines = []
    for line in data.get("lines", []):
        medicine = conn.execute("SELECT id FROM medicines WHERE cloud_uuid=?", (line.get("cloud_medicine_id"),)).fetchone()
        batch = conn.execute("SELECT id FROM medicine_batches WHERE cloud_uuid=?", (line.get("cloud_batch_id"),)).fetchone()
        if not medicine or not batch:
            _flag_remote_conflict(conn, event_id, "remote_sale_catalogue_missing", {"line": line})
            return
        lines.append((line, medicine["id"], batch["id"]))
    local_sale_id = conn.execute(
        "INSERT INTO sales(invoice_no,customer_id,cashier_id,date,total_amount,discount,tax,payment_method,amount_paid,doctor_name,transaction_id,shop_id,device_id) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (data["invoice_no"], customer["id"] if customer else None, cashier_id, data["occurred_at"],
         data["total_amount"], data.get("discount", 0), data.get("tax", 0), data.get("payment_method", "cash"),
         data.get("amount_paid", 0), data.get("doctor_name"), transaction_id, data.get("shop_id"), data.get("device_id")),
    ).lastrowid
    conn.execute("UPDATE sync_outbox SET status='acked',acknowledged_at=? WHERE event_id=?", (utc_now(), event_id))
    for line, medicine_id, batch_id in lines:
        conn.execute("INSERT INTO sale_items(sale_id,medicine_id,quantity,unit_price,subtotal,batch_id,unit_cost,cloud_uuid) VALUES(?,?,?,?,?,?,?,?)",
                     (local_sale_id, medicine_id, line["quantity"], line["unit_price"], line["subtotal"],
                      batch_id, line["unit_cost"], line.get("cloud_sale_item_id")))
        # The local catalogue count may have changed while the terminal was
        # disconnected. Apply movement deltas or expose a reconciliation conflict.
        row = conn.execute("SELECT quantity FROM medicine_batches WHERE id=?", (batch_id,)).fetchone()
        if row["quantity"] < line["quantity"]:
            _flag_remote_conflict(conn, event_id, "stale_local_stock", {"batch_id": batch_id})
            continue
        conn.execute("UPDATE medicine_batches SET quantity=quantity-? WHERE id=?", (line["quantity"], batch_id))
        conn.execute("UPDATE medicines SET quantity=quantity-? WHERE id=?", (line["quantity"], medicine_id))
        conn.execute("INSERT INTO stock_movements(id,transaction_id,medicine_id,batch_id,movement_type,quantity_delta,source_event_id,occurred_at,details) "
                     "VALUES(?,?,?,?,?,?,?,?,?)",
                     (str(uuid.uuid4()), transaction_id, medicine_id, batch_id, "remote_sale", -line["quantity"],
                      event_id, data["occurred_at"], json.dumps({"invoice_no": data["invoice_no"]})))
    if customer and data.get("total_amount", 0) > data.get("amount_paid", 0):
        conn.execute("UPDATE customers SET credit_balance=credit_balance+? WHERE id=?",
                     (data["total_amount"] - data.get("amount_paid", 0), customer["id"]))
    paid = float(data.get("amount_paid", 0))
    if paid > 0:
        conn.execute(
            "INSERT OR IGNORE INTO local_payments(id,transaction_id,sale_id,purchase_id,amount,method,status,occurred_at) "
            "VALUES(?,?,?,NULL,?,?,?,?)",
            (data.get("payment_id") or str(uuid.uuid4()), str(uuid.uuid4()), local_sale_id,
             paid, data.get("payment_method", "cash"), "recorded", data["occurred_at"]),
        )


def _import_remote_purchase(conn: sqlite3.Connection, data: dict, transaction_id: str, event_id: str) -> None:
    # Purchases are downloaded through the stock movement stream. If the
    # supplier/batch identity is unknown, the conflict remains visible.
    supplier = conn.execute("SELECT id FROM suppliers WHERE cloud_uuid=?", (data.get("cloud_supplier_id"),)).fetchone()
    medicine = conn.execute("SELECT id FROM medicines WHERE cloud_uuid=?", (data.get("cloud_medicine_id"),)).fetchone()
    if not supplier or not medicine:
        _flag_remote_conflict(conn, event_id, "remote_purchase_catalogue_missing", {"purchase_id": data.get("purchase_id")})
        return
    if conn.execute("SELECT 1 FROM local_purchases WHERE transaction_id=?", (transaction_id,)).fetchone():
        return
    batch_id = data.get("cloud_batch_id")
    local = conn.execute("SELECT id FROM medicine_batches WHERE cloud_uuid=?", (batch_id,)).fetchone()
    if not local:
        local_id = batch_id
        conn.execute("INSERT INTO medicine_batches(id,medicine_id,cloud_uuid,batch_no,expiry_date,quantity,purchase_price,sale_price,source) VALUES(?,?,?,?,?,0,?,?, 'cloud')",
                     (local_id, medicine["id"], batch_id, data["batch_no"], data.get("expiry_date"),
                      data.get("purchase_cost", 0), data.get("selling_price", 0)))
    else:
        local_id = local["id"]
    quantity = int(data["quantity"])
    occurred_at = data.get("occurred_at", utc_now())
    purchase_id = data.get("purchase_id") or str(uuid.uuid4())
    total = float(data.get("total_cost", float(data["purchase_cost"]) * quantity))
    paid = float(data.get("amount_paid", total))
    conn.execute("INSERT OR IGNORE INTO local_purchases(id,transaction_id,supplier_id,total_amount,amount_paid,status,occurred_at) VALUES(?,?,?,?,?,?,?)",
                 (purchase_id, transaction_id, supplier["id"], total, paid,
                  "paid" if paid >= total else "partially_paid", occurred_at))
    conn.execute("INSERT INTO local_purchase_items(id,purchase_id,medicine_id,batch_id,quantity,purchase_cost,selling_price) VALUES(?,?,?,?,?,?,?)",
                 (str(uuid.uuid4()), purchase_id, medicine["id"], local_id, quantity,
                  data["purchase_cost"], data.get("selling_price", 0)))
    conn.execute("INSERT OR IGNORE INTO local_payments(id,transaction_id,sale_id,purchase_id,amount,method,status,occurred_at) VALUES(?,?,NULL,?,?,?,?,?)",
                 (str(uuid.uuid4()), str(uuid.uuid4()), purchase_id, paid, "cash",
                  "recorded" if paid else "unpaid", occurred_at))
    conn.execute("UPDATE medicine_batches SET quantity=quantity+? WHERE id=?", (quantity, local_id))
    conn.execute("UPDATE medicines SET quantity=quantity+? WHERE id=?", (quantity, medicine["id"]))
    conn.execute("UPDATE suppliers SET payable_balance=payable_balance+? WHERE id=?",
                 (total-paid, supplier["id"]))


def _apply_remote_movements(conn: sqlite3.Connection, data: dict, transaction_id: str, event_id: str) -> None:
    for movement in data.get("movements", []):
        cloud_batch_id = movement.get("cloud_batch_id")
        batch = conn.execute("SELECT id,medicine_id,quantity FROM medicine_batches WHERE cloud_uuid=?", (cloud_batch_id,)).fetchone()
        delta = int(movement.get("quantity_delta", movement.get("delta", 0)))
        if batch is None:
            _flag_remote_conflict(conn, event_id, "remote_batch_missing", {"cloud_batch_id": cloud_batch_id})
            continue
        if batch["quantity"] + delta < 0:
            _flag_remote_conflict(conn, event_id, "remote_stock_conflict", {"cloud_batch_id": cloud_batch_id, "delta": delta})
            continue
        conn.execute("UPDATE medicine_batches SET quantity=quantity+? WHERE id=?", (delta, batch["id"]))
        conn.execute("UPDATE medicines SET quantity=quantity+? WHERE id=?", (delta, batch["medicine_id"]))
        conn.execute("INSERT INTO stock_movements(id,transaction_id,medicine_id,batch_id,movement_type,quantity_delta,source_event_id,occurred_at,details) VALUES(?,?,?,?,?,?,?,?,?)",
                     (str(uuid.uuid4()), transaction_id, batch["medicine_id"], batch["id"], "remote_adjustment",
                      delta, event_id, data.get("occurred_at", utc_now()), "{}"))


def apply_cloud_bootstrap(snapshot: dict) -> None:
    """Import an authorized cloud catalogue without overwriting local history.

    Existing unmatched stock is retained and flagged for reconciliation.
    New cloud records are imported with their UUID mappings so offline sale
    events can refer to the same batches when replayed.
    """
    from database.db_manager import get_connection

    tenant_id, shop_id = snapshot["tenant_id"], snapshot["shop_id"]
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        identity = conn.execute("SELECT * FROM sync_identity WHERE singleton=1").fetchone()
        if identity["tenant_id"] and (identity["tenant_id"] != tenant_id or identity["shop_id"] != shop_id):
            raise ValueError("This local database is already linked to a different tenant or shop.")
        medicine_map = {}
        for remote in snapshot.get("medicines", []):
            local = conn.execute("SELECT * FROM medicines WHERE cloud_uuid=?", (remote["id"],)).fetchone()
            if local is None and remote.get("barcode"):
                local = conn.execute("SELECT * FROM medicines WHERE barcode=? AND is_active=1", (remote["barcode"],)).fetchone()
            if local is None:
                candidates = conn.execute(
                    "SELECT * FROM medicines WHERE name=? AND COALESCE(generic_name,'')=COALESCE(?,'') AND is_active=1",
                    (remote["name"], remote.get("generic_name")),
                ).fetchall()
                if len(candidates) == 1:
                    local = candidates[0]
            if local is None:
                cur = conn.execute(
                    "INSERT INTO medicines(name,generic_name,manufacturer,category,barcode,quantity,purchase_price,sale_price,cloud_uuid,cloud_sku,is_controlled_substance) "
                    "VALUES(?,?,?,?,?,0,?,?,?,?,?)",
                    (remote["name"], remote.get("generic_name"), remote.get("manufacturer"),
                     remote.get("category"), remote.get("barcode"), 0, 0, remote["id"],
                     remote.get("sku"), int(remote.get("is_controlled", False))),
                )
                local_id = cur.lastrowid
            else:
                local_id = local["id"]
                if local["cloud_uuid"] and local["cloud_uuid"] != remote["id"]:
                    _flag_remote_conflict(conn, remote["id"], "catalogue_identity_collision",
                                          {"local_medicine_id": local_id, "cloud_medicine_id": remote["id"]})
                    continue
                conn.execute(
                    "UPDATE medicines SET cloud_uuid=?,cloud_sku=?,name=?,generic_name=?,manufacturer=?,category=?,barcode=?,is_controlled_substance=? WHERE id=?",
                    (remote["id"], remote.get("sku"), remote["name"], remote.get("generic_name"),
                     remote.get("manufacturer"), remote.get("category"), remote.get("barcode"),
                     int(remote.get("is_controlled", False)), local_id),
                )
            medicine_map[remote["id"]] = local_id

        for remote in snapshot.get("suppliers", []):
            existing = conn.execute("SELECT id FROM suppliers WHERE cloud_uuid=?", (remote["id"],)).fetchone()
            if existing:
                conn.execute("UPDATE suppliers SET name=?,contact=?,payable_balance=? WHERE id=?",
                             (remote["name"], remote.get("phone"), float(remote.get("payable_balance", 0)), existing["id"]))
            else:
                conn.execute("INSERT INTO suppliers(name,contact,payable_balance,cloud_uuid) VALUES(?,?,?,?)",
                             (remote["name"], remote.get("phone"), float(remote.get("payable_balance", 0)), remote["id"]))
        for remote in snapshot.get("customers", []):
            existing = conn.execute("SELECT id FROM customers WHERE cloud_uuid=?", (remote["id"],)).fetchone()
            if existing:
                conn.execute("UPDATE customers SET name=?,phone=?,credit_balance=? WHERE id=?",
                             (remote["name"], remote.get("phone"), float(remote.get("credit_balance", 0)), existing["id"]))
            else:
                conn.execute("INSERT INTO customers(name,phone,credit_balance,cloud_uuid) VALUES(?,?,?,?)",
                             (remote["name"], remote.get("phone"), float(remote.get("credit_balance", 0)), remote["id"]))

        for remote in snapshot.get("batches", []):
            medicine_id = medicine_map.get(remote["medicine_id"])
            if medicine_id is None:
                _flag_remote_conflict(conn, remote["id"], "batch_without_medicine", {"batch_id": remote["id"]})
                continue
            local = conn.execute("SELECT * FROM medicine_batches WHERE cloud_uuid=?", (remote["id"],)).fetchone()
            if local is None:
                local = conn.execute("SELECT * FROM medicine_batches WHERE medicine_id=? AND batch_no=?",
                                     (medicine_id, remote["batch_no"])).fetchone()
            if local is None:
                stock = conn.execute("SELECT quantity FROM medicines WHERE id=?", (medicine_id,)).fetchone()[0]
                if stock:
                    _flag_remote_conflict(conn, remote["id"], "legacy_stock_requires_reconciliation",
                                          {"medicine_id": medicine_id, "local_quantity": stock,
                                           "cloud_quantity": remote["quantity"]})
                    continue
                local_id = remote["id"]
                conn.execute(
                    "INSERT INTO medicine_batches(id,medicine_id,cloud_uuid,batch_no,expiry_date,quantity,purchase_price,sale_price,source) "
                    "VALUES(?,?,?,?,?,?,?,?,'cloud')",
                    (local_id, medicine_id, remote["id"], remote["batch_no"], remote["expiry_date"],
                     remote["quantity"], float(remote["purchase_cost"]), float(remote["selling_price"])),
                )
                continue
            if local["cloud_uuid"] and local["cloud_uuid"] != remote["id"]:
                _flag_remote_conflict(conn, remote["id"], "batch_identity_collision", {"local_batch_id": local["id"]})
                continue
            local_pending = conn.execute("SELECT payload FROM sync_outbox WHERE status IN ('pending','failed','inflight')").fetchall()
            has_pending = _pending_affects_batch([json.loads(row[0]) for row in local_pending], local["id"])
            if not identity["tenant_id"] and local["quantity"] != remote["quantity"]:
                _flag_remote_conflict(conn, remote["id"], "legacy_stock_requires_reconciliation",
                                      {"local_quantity": local["quantity"], "cloud_quantity": remote["quantity"]})
                continue
            if has_pending and local["quantity"] != remote["quantity"]:
                _flag_remote_conflict(conn, remote["id"], "local_stock_changes_pending",
                                      {"local_quantity": local["quantity"], "cloud_quantity": remote["quantity"]})
                continue
            conn.execute(
                "UPDATE medicine_batches SET cloud_uuid=?,expiry_date=?,quantity=?,purchase_price=?,sale_price=? WHERE id=?",
                (remote["id"], remote["expiry_date"], remote["quantity"], float(remote["purchase_cost"]),
                 float(remote["selling_price"]), local["id"]),
            )
        for cloud_id in medicine_map:
            conn.execute(
                "UPDATE medicines SET quantity=COALESCE((SELECT SUM(quantity) FROM medicine_batches b WHERE b.medicine_id=medicines.id),0) WHERE cloud_uuid=?",
                (cloud_id,),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _cli():
    import argparse
    from getpass import getpass

    parser = argparse.ArgumentParser(description="Configure this SOLONA Pharmacy installation")
    commands = parser.add_subparsers(dest="command", required=True)
    lan = commands.add_parser("configure-lan", help="Enroll with a shop-local coordinator")
    lan.add_argument("--url", required=True, help="Shop server URL, for example http://192.168.1.20:8765")
    lan.add_argument("--name", default="This computer")
    args = parser.parse_args()
    if args.command == "configure-lan":
        enrollment_secret = getpass("One-time shop server enrollment secret: ")
        device_id = configure_lan_coordinator(args.url, enrollment_secret, args.name)
        print(f"This terminal is enrolled as {device_id}. The device key is stored in the OS credential store.")


if __name__ == "__main__":
    _cli()
