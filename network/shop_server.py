"""Small SQLite-backed shop coordinator.

This process owns its private database; terminals never open its SQLite file
over the network. Run `python -m network.shop_server --seed-from <db>` once to
load the shop's opening catalogue, then run it on a trusted shop LAN. All
writes are serialized with BEGIN IMMEDIATE and keyed by immutable event ID.
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional, List, Dict, Tuple, Union

from logic.sync import utc_now


class ShopCoordinator:
    def __init__(self, database: Union[str, Path]):
        self.path = str(database)
        master_key = os.environ.get("SOLONA_LAN_MASTER_KEY", "")
        if master_key:
            try:
                self._master_key = bytes.fromhex(master_key)
            except ValueError as exc:
                raise ValueError("SOLONA_LAN_MASTER_KEY must be 64 hexadecimal characters.") from exc
            if len(self._master_key) != 32:
                raise ValueError("SOLONA_LAN_MASTER_KEY must contain 32 bytes.")
        else:
            key_path = Path(self.path + ".key")
            key_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                self._master_key = bytes.fromhex(key_path.read_text(encoding="ascii").strip())
            else:
                self._master_key = secrets.token_bytes(32)
                with os.fdopen(descriptor, "w", encoding="ascii") as key_file:
                    key_file.write(self._master_key.hex())
        if len(self._master_key) != 32:
            raise ValueError("LAN coordinator master key must contain 32 bytes.")
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        self._cipher = AESGCM(self._master_key)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS batches(
                    id TEXT PRIMARY KEY, medicine_id INTEGER NOT NULL, batch_no TEXT NOT NULL,
                    quantity INTEGER NOT NULL CHECK(quantity>=0), expiry_date TEXT,
                    UNIQUE(medicine_id,batch_no));
                CREATE TABLE IF NOT EXISTS processed_events(
                    event_id TEXT PRIMARY KEY, transaction_id TEXT NOT NULL,
                    event_type TEXT NOT NULL, payload TEXT NOT NULL, result TEXT NOT NULL,
                    occurred_at TEXT NOT NULL, processed_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS changes(
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS conflicts(
                    conflict_id TEXT PRIMARY KEY, event_id TEXT NOT NULL,
                    details TEXT NOT NULL, created_at TEXT NOT NULL, resolved INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS devices(
                    device_id TEXT PRIMARY KEY, name TEXT NOT NULL, credential_hash TEXT NOT NULL,
                    registered_at TEXT NOT NULL, revoked_at TEXT);
            """)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        return db

    def seed_from_local(self, source: Union[str, Path]):
        source_db = sqlite3.connect(str(source))
        try:
            batch_rows = source_db.execute(
                "SELECT id,medicine_id,batch_no,quantity,expiry_date FROM medicine_batches"
            ).fetchall()
            event_rows = source_db.execute(
                "SELECT event_id,transaction_id,event_type,payload,occurred_at FROM sync_outbox "
                "WHERE status IN ('pending','failed','inflight') ORDER BY occurred_at"
            ).fetchall()
        finally:
            source_db.close()
        with self._lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM processed_events LIMIT 1").fetchone():
                raise RuntimeError("This coordinator already has transaction history; refuse to reseed it.")
            db.execute("DELETE FROM batches")
            db.executemany("INSERT INTO batches VALUES(?,?,?,?,?)", batch_rows)
            for event_id, transaction_id, event_type, payload, occurred_at in event_rows:
                result = json.dumps({"event_id": event_id, "status": "accepted", "baseline": True})
                db.execute("INSERT OR IGNORE INTO processed_events VALUES(?,?,?,?,?,?,?)",
                           (event_id, transaction_id, event_type, payload, result, occurred_at, utc_now()))
            db.commit()
        source_db = sqlite3.connect(str(source))
        try:
            source_db.execute("UPDATE sync_outbox SET status='acked',acknowledged_at=?,last_error=NULL WHERE status IN ('pending','failed','inflight')",
                              (utc_now(),))
            source_db.execute("UPDATE sync_identity SET last_successful_sync=?,last_error=NULL WHERE singleton=1",
                              (utc_now(),))
            source_db.commit()
        finally:
            source_db.close()

    def process_event(self, event: dict) -> dict:
        event_id = str(uuid.UUID(event["event_id"]))
        transaction_id = str(uuid.UUID(event["transaction_id"]))
        event_type = event["event_type"]
        payload = event["payload"]
        with self._lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT result FROM processed_events WHERE event_id=?", (event_id,)).fetchone()
            if prior:
                db.commit()
                return {**json.loads(prior["result"]), "duplicate": True}
            same_transaction = db.execute(
                "SELECT event_id FROM processed_events WHERE transaction_id=?", (transaction_id,)
            ).fetchone()
            if same_transaction:
                result = {"event_id": event_id, "status": "conflict", "code": "duplicate_transaction"}
                self._save_conflict(db, event_id, result)
                self._record_event(db, event, event_id, transaction_id, event_type, result)
                db.commit()
                return result

            movements = self._movements(event_type, payload)
            for batch_id, delta in movements:
                row = db.execute("SELECT quantity FROM batches WHERE id=?", (batch_id,)).fetchone()
                if row is None and delta > 0:
                    db.execute(
                        "INSERT INTO batches(id,medicine_id,batch_no,quantity,expiry_date) VALUES(?,?,?,?,?)",
                        (batch_id, int(payload.get("medicine_id", 0)),
                         str(payload.get("batch_no", f"REMOTE-{batch_id[:8]}")), 0,
                         payload.get("expiry_date")),
                    )
                    row = db.execute("SELECT quantity FROM batches WHERE id=?", (batch_id,)).fetchone()
                if row is None:
                    result = {"event_id": event_id, "status": "conflict", "code": "unknown_batch", "batch_id": batch_id}
                    self._save_conflict(db, event_id, result)
                    self._record_event(db, event, event_id, transaction_id, event_type, result)
                    db.commit()
                    return result
                if row["quantity"] + delta < 0:
                    result = {"event_id": event_id, "status": "conflict", "code": "stale_stock", "batch_id": batch_id,
                              "server_quantity": row["quantity"], "requested_delta": delta}
                    self._save_conflict(db, event_id, result)
                    self._record_event(db, event, event_id, transaction_id, event_type, result)
                    db.commit()
                    return result
            for batch_id, delta in movements:
                db.execute("UPDATE batches SET quantity=quantity+? WHERE id=?", (delta, batch_id))
            result = {"event_id": event_id, "status": "accepted", "duplicate": False}
            canonical = {"event_id": event_id, "transaction_id": transaction_id,
                         "event_type": event_type, "payload": payload, "movements": movements}
            self._record_event(db, event, event_id, transaction_id, event_type, result, canonical)
            db.execute("INSERT INTO changes(event_id,event_type,payload,created_at) VALUES(?,?,?,?)",
                       (event_id, event_type, json.dumps(canonical), utc_now()))
            db.commit()
            return result

    @staticmethod
    def _movements(event_type: str, payload: dict) -> List[Tuple[str, int]]:
        if event_type == "sale.created":
            return [(str(line["batch_id"]), -int(line["quantity"])) for line in payload.get("lines", [])]
        if event_type == "sale.returned":
            return [(str(payload["batch_id"]), int(payload["quantity"]))]
        if event_type in {"purchase.received", "medicine.created"}:
            batch_id = payload.get("batch_id")
            quantity = int(payload.get("quantity", 0))
            return [(str(batch_id), quantity)] if batch_id and quantity else []
        if event_type in {"stock.adjusted", "medicine.updated"}:
            source = payload.get("movements", [])
            return [(str(item["batch_id"]), int(item.get("quantity_delta", item.get("delta", 0)))) for item in source]
        return []

    @staticmethod
    def _save_conflict(db, event_id: str, details: dict):
        db.execute("INSERT INTO conflicts VALUES(?,?,?, ?,0)",
                   (str(uuid.uuid4()), event_id, json.dumps(details), utc_now()))

    @staticmethod
    def _record_event(db, event: dict, event_id: str, transaction_id: str,
                      event_type: str, result: dict, canonical: Optional[dict] = None):
        canonical = canonical or event
        db.execute(
            "INSERT OR IGNORE INTO processed_events VALUES(?,?,?,?,?,?,?)",
            (event_id, transaction_id, event_type, json.dumps(canonical), json.dumps(result),
             event.get("occurred_at") or utc_now(), utc_now()),
        )

    def pull(self, cursor: int = 0, limit: int = 200) -> dict:
        with self.connect() as db:
            rows = db.execute("SELECT seq,event_id,event_type,payload FROM changes WHERE seq>? ORDER BY seq LIMIT ?",
                              (cursor, min(max(limit, 1), 500))).fetchall()
            return {"changes": [{"cursor": r["seq"], "event_id": r["event_id"],
                                  "event_type": r["event_type"], "payload": json.loads(r["payload"])} for r in rows],
                    "cursor": rows[-1]["seq"] if rows else cursor}

    def batch_snapshot(self) -> List[Dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM batches ORDER BY medicine_id,expiry_date,batch_no")]

    def enroll_device(self, device_id: str, name: str) -> str:
        device_id = str(uuid.UUID(device_id))
        credential = secrets.token_urlsafe(40)
        encrypted_credential = self._encrypt_credential(credential)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT revoked_at FROM devices WHERE device_id=?", (device_id,)).fetchone()
            if existing:
                db.rollback()
                if existing["revoked_at"]:
                    raise PermissionError("A revoked device requires explicit administrator reinstatement.")
                raise ValueError("Device is already enrolled.")
            db.execute("INSERT INTO devices VALUES(?,?,?,?,NULL)",
                       (device_id, name[:120], encrypted_credential, utc_now()))
            db.commit()
        return credential

    def _encrypt_credential(self, credential: str) -> str:
        nonce = secrets.token_bytes(12)
        ciphertext = self._cipher.encrypt(nonce, credential.encode(), b"solona-lan-device-v1")
        return "enc:" + base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")

    def revoke_device(self, device_id: str) -> bool:
        device_id = str(uuid.UUID(device_id))
        with self.connect() as db:
            cursor = db.execute("UPDATE devices SET revoked_at=? WHERE device_id=? AND revoked_at IS NULL",
                                (utc_now(), device_id))
            return cursor.rowcount == 1

    def reinstate_device(self, device_id: str, name: str | None = None) -> str:
        """Rotate credentials while explicitly restoring a revoked terminal."""
        device_id = str(uuid.UUID(device_id))
        credential = secrets.token_urlsafe(40)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT name,revoked_at FROM devices WHERE device_id=?", (device_id,)).fetchone()
            if row is None or row["revoked_at"] is None:
                db.rollback()
                raise ValueError("Only a revoked, registered device can be reinstated.")
            db.execute("UPDATE devices SET name=?,credential_hash=?,registered_at=?,revoked_at=NULL WHERE device_id=?",
                       ((name or row["name"])[:120], self._encrypt_credential(credential),
                        utc_now(), device_id))
            db.commit()
        return credential

    def device_secret(self, device_id: str):
        with self.connect() as db:
            row = db.execute("SELECT credential_hash,revoked_at FROM devices WHERE device_id=?", (device_id,)).fetchone()
            if row is None or row["revoked_at"]:
                return None
            stored = row["credential_hash"]
            if stored.startswith("enc:"):
                blob = base64.urlsafe_b64decode(stored[4:].encode("ascii"))
                credential = self._cipher.decrypt(blob[:12], blob[12:], b"solona-lan-device-v1")
                hmac_key = hashlib.sha256(credential).digest()
            else:
                # Existing coordinators retain compatibility until devices are
                # explicitly re-enrolled; migrate these rows during rotation.
                hmac_key = bytes.fromhex(stored)
            return {"hmac_key": hmac_key, "revoked_at": row["revoked_at"]}

    def verify_device_request(self, device_id: str, path: str, body: bytes,
                              timestamp: int, signature: str, now: Optional[int] = None) -> bool:
        row = self.device_secret(device_id)
        if row is None:
            return False
        now = now if now is not None else int(datetime.now(timezone.utc).timestamp())
        if abs(now - timestamp) > 300:
            return False
        expected = hmac.new(row["hmac_key"],
                            str(timestamp).encode() + b"." + path.encode() + b"." + body,
                            hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected)

    def device_can_request(self, device_id: str, timestamp: int,
                           now: Optional[int] = None) -> bool:
        if self.device_secret(device_id) is None:
            return False
        now = now if now is not None else int(datetime.now(timezone.utc).timestamp())
        return abs(now - timestamp) <= 300


def make_handler(coordinator: ShopCoordinator, enrollment_secret: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "SolonaShopCoordinator/1"
        request_timeout = 5

        def setup(self):
            super().setup()
            self.connection.settimeout(self.request_timeout)

        def log_message(self, format, *args):
            # Avoid logging request bodies, credentials, or patient data.
            return

        def _json(self, status, payload):
            body = json.dumps(payload, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/health":
                return self._json(200, {"status": "ok"})
            if not self._authenticated(b""):
                return self._json(401, {"detail": "Authentication failed"})
            if self.path.startswith("/changes"):
                query = self.path.split("?", 1)[-1]
                args = dict(part.split("=", 1) for part in query.split("&") if "=" in part)
                return self._json(200, coordinator.pull(int(args.get("cursor", "0"))))
            if self.path == "/batches":
                return self._json(200, {"batches": coordinator.batch_snapshot()})
            return self._json(404, {"detail": "Not found"})

        def do_POST(self):
            raw_length = self.headers.get("Content-Length")
            try:
                length = int(raw_length) if raw_length is not None else -1
            except ValueError:
                return self._json(400, {"detail": "Invalid Content-Length"})
            if length < 0:
                return self._json(400, {"detail": "Invalid Content-Length"})
            if length > 1_000_000:
                return self._json(413, {"detail": "Request body is too large"})
            if self.path == "/enroll":
                supplied = self.headers.get("X-Shani-Enrollment", "")
                if not hmac.compare_digest(supplied, enrollment_secret):
                    return self._json(401, {"detail": "Authentication failed"})
                try:
                    body = self.rfile.read(length)
                    data = json.loads(body)
                    device_id = str(uuid.UUID(data["device_id"]))
                    credential = coordinator.enroll_device(device_id, data.get("name", "Terminal"))
                    return self._json(201, {"device_id": device_id, "device_credential": credential})
                except PermissionError:
                    return self._json(403, {"detail": "A revoked device requires administrator reinstatement"})
                except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                    return self._json(422, {"detail": "Invalid enrollment"})
            if self.path.startswith("/devices/") and self.path.endswith("/reinstate"):
                supplied = self.headers.get("X-Shani-Enrollment", "")
                if not hmac.compare_digest(supplied, enrollment_secret):
                    return self._json(401, {"detail": "Authentication failed"})
                try:
                    device_id = str(uuid.UUID(self.path.split("/")[2]))
                    data = json.loads(self.rfile.read(length) or b"{}")
                    credential = coordinator.reinstate_device(device_id, data.get("name"))
                    return self._json(200, {"device_id": device_id, "device_credential": credential})
                except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                    return self._json(422, {"detail": "Invalid reinstatement"})
            if self.path != "/events":
                return self._json(404, {"detail": "Not found"})
            try:
                device_id = self.headers.get("X-Shani-Device", "")
                timestamp = int(self.headers.get("X-Shani-Time", "0"))
                if not coordinator.device_can_request(device_id, timestamp):
                    return self._json(401, {"detail": "Authentication failed"})
                body = self.rfile.read(length)
                if not self._authenticated(body):
                    return self._json(401, {"detail": "Authentication failed"})
                event = json.loads(body)
                if event.get("device_id") != self.headers.get("X-Shani-Device"):
                    return self._json(401, {"detail": "Authentication failed"})
                result = coordinator.process_event(event)
                return self._json(200 if result["status"] == "accepted" else 409, result)
            except (ValueError, KeyError, TypeError, json.JSONDecodeError, TimeoutError):
                return self._json(422, {"detail": "Invalid event"})

        def do_DELETE(self):
            if not self.path.startswith("/devices/"):
                return self._json(404, {"detail": "Not found"})
            supplied = self.headers.get("X-Shani-Enrollment", "")
            if not hmac.compare_digest(supplied, enrollment_secret):
                return self._json(401, {"detail": "Authentication failed"})
            try:
                device_id = str(uuid.UUID(self.path.rsplit("/", 1)[-1]))
            except ValueError:
                return self._json(422, {"detail": "Invalid device"})
            if not coordinator.revoke_device(device_id):
                return self._json(404, {"detail": "Device not found"})
            return self._json(200, {"device_id": device_id, "status": "revoked"})

        def _authenticated(self, body: bytes) -> bool:
            device_id = self.headers.get("X-Shani-Device", "")
            try:
                timestamp = int(self.headers.get("X-Shani-Time", "0"))
            except ValueError:
                return False
            supplied = self.headers.get("X-Shani-Signature", "")
            return coordinator.verify_device_request(device_id, self.path, body, timestamp, supplied)

    return Handler


def main():
    parser = argparse.ArgumentParser(description="Run the optional SOLONA shop-local transaction coordinator")
    parser.add_argument("--database", default=os.environ.get(
        "SOLONA_LAN_DB", os.environ.get("SPMS_LAN_DB", "data/shop-coordinator.db")))
    parser.add_argument("--seed-from", help="Import opening batches from a local pharmacy.db before serving")
    parser.add_argument("--host", default=os.environ.get(
        "SOLONA_LAN_HOST", os.environ.get("SPMS_LAN_HOST", "127.0.0.1")))
    parser.add_argument("--port", type=int, default=int(os.environ.get(
        "SOLONA_LAN_PORT", os.environ.get("SPMS_LAN_PORT", "8765"))))
    args = parser.parse_args()
    secret_text = os.environ.get(
        "SOLONA_LAN_ENROLLMENT_SECRET", os.environ.get("SPMS_LAN_ENROLLMENT_SECRET", ""))
    if not secret_text:
        secret_text = secrets.token_urlsafe(48)
        print(f"One-time terminal enrollment secret (store securely): {secret_text}")
    coordinator = ShopCoordinator(args.database)
    if args.seed_from:
        coordinator.seed_from_local(args.seed_from)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(coordinator, secret_text))
    print(f"Shop coordinator listening on {args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
