"""SQLite connection management and schema initialization.

Resolves paths relative to the project root during normal development, so
`python main.py` reads/writes files right there in the repo. When frozen by
PyInstaller, data instead lives under the current user's %LOCALAPPDATA%
(NOT next to the .exe): the installer may place the executable in
Program Files, which a normal (non-elevated) running process cannot write
to, so storing data there would break saving on first use.
"""
import os
import sqlite3
import sys
import uuid
from pathlib import Path

from logic.security import hash_password


def get_app_dir() -> Path:
    """Directory that holds data/, backups/, and invoices/."""
    if getattr(sys, "frozen", False):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        return Path(base) / "PharmacyManagementSystem"
    return Path(__file__).resolve().parent.parent


APP_DIR = get_app_dir()
DATA_DIR = APP_DIR / "data"
BACKUP_DIR = APP_DIR / "backups"
DB_PATH = Path(os.environ.get("SOLONA_DB_PATH", os.environ.get("SPMS_DB_PATH", str(DATA_DIR / "pharmacy.db"))))
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

DEFAULT_ADMIN_USERNAME = "admin"


def get_connection() -> sqlite3.Connection:
    """Open a new connection with sane defaults (foreign keys, dict-like rows)."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """ALTER TABLE ... ADD COLUMN if it's not already there. CREATE TABLE IF
    NOT EXISTS in schema.sql is a no-op on an already-existing table, so
    every column added to an existing table after its first release needs
    an explicit migration step like this one for older installs to pick it
    up without losing their data."""
    columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})")]
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        conn.commit()


def _run_migrations(conn: sqlite3.Connection) -> None:
    _add_column_if_missing(conn, "medicines", "barcode", "TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_medicines_barcode ON medicines(barcode)")

    _add_column_if_missing(conn, "medicines", "is_controlled_substance", "INTEGER NOT NULL DEFAULT 0")
    _add_column_if_missing(conn, "customers", "credit_balance", "REAL NOT NULL DEFAULT 0")
    _add_column_if_missing(conn, "sales", "payment_method", "TEXT NOT NULL DEFAULT 'cash'")
    _add_column_if_missing(conn, "sales", "amount_paid", "REAL NOT NULL DEFAULT 0")
    _add_column_if_missing(conn, "sales", "doctor_name", "TEXT")
    _add_column_if_missing(conn, "sales", "is_refunded", "INTEGER NOT NULL DEFAULT 0")
    _add_column_if_missing(conn, "sale_items", "returned_qty", "INTEGER NOT NULL DEFAULT 0")
    _add_column_if_missing(conn, "medicines", "manufacturer", "TEXT")
    _add_column_if_missing(conn, "medicines", "units_per_pack", "INTEGER NOT NULL DEFAULT 1")
    _add_column_if_missing(conn, "suppliers", "payable_balance", "REAL NOT NULL DEFAULT 0")
    conn.commit()

    # Older sales predate amount_paid tracking (it defaults to 0). Backfill
    # them as fully paid in cash exactly once -- this must NOT re-run on
    # every startup, since a legitimate 100%-credit sale also has
    # amount_paid=0 by design and would otherwise get silently "paid off"
    # the next time the app starts.
    already_backfilled = conn.execute(
        "SELECT 1 FROM settings WHERE key='amount_paid_backfilled'"
    ).fetchone()
    if not already_backfilled:
        conn.execute(
            "UPDATE sales SET amount_paid = total_amount, payment_method = 'cash' "
            "WHERE amount_paid = 0 AND payment_method = 'cash'"
        )
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('amount_paid_backfilled', '1')"
        )
        conn.commit()

    _run_stage3_migrations(conn)


def init_db() -> None:
    """Create tables if missing and seed the first administrator securely."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    conn = get_connection()
    try:
        conn.executescript(schema_sql)
        conn.commit()

        _run_migrations(conn)

        cur = conn.execute("SELECT COUNT(*) AS c FROM users")
        if cur.fetchone()["c"] == 0:
            import secrets

            bootstrap_password = secrets.token_urlsafe(24)
            if getattr(sys, "frozen", False):
                try:
                    import keyring

                    keyring.set_password("ShaniPharmacy", "first_run_admin", bootstrap_password)
                except Exception as exc:
                    raise RuntimeError(
                        "Could not securely provision the first-run administrator password. "
                        "Install/configure Windows Credential Manager and try again."
                    ) from exc
            conn.execute(
                "INSERT INTO users (username, password_hash, full_name, role) "
                "VALUES (?, ?, ?, 'admin')",
                (
                    DEFAULT_ADMIN_USERNAME,
                    hash_password(bootstrap_password),
                    "Administrator",
                ),
            )
            conn.commit()
            if not getattr(sys, "frozen", False):
                print("First-run administrator created. Username: admin")
                print(f"Temporary password (store securely and change after sign-in): {bootstrap_password}")

        # Seed default configurable settings if absent.
        defaults = {
            "low_stock_threshold": "10",
            "expiry_alert_days": "30",
        }
        for key, value in defaults.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )
        conn.commit()
    finally:
        conn.close()


def _run_stage3_migrations(conn: sqlite3.Connection) -> None:
    """Apply additive, numbered local/offline migrations transactionally."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    applied = conn.execute("SELECT 1 FROM schema_migrations WHERE version=3").fetchone()
    if applied:
        return

    try:
        conn.executescript(
            "BEGIN IMMEDIATE;\n" + """
            CREATE TABLE sync_identity (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                tenant_id TEXT, tenant_name TEXT,
                shop_id TEXT, shop_name TEXT,
                device_id TEXT NOT NULL UNIQUE,
                device_name TEXT NOT NULL DEFAULT 'This computer',
                linked_at TEXT,
                last_successful_sync TEXT,
                last_error TEXT,
                coordination_mode TEXT NOT NULL DEFAULT 'standalone'
                    CHECK(coordination_mode IN ('standalone','lan_client','lan_server','cloud_client')),
                lan_server_url TEXT,
                cloud_api_url TEXT
            );
            CREATE TABLE medicine_batches (
                id TEXT PRIMARY KEY,
                medicine_id INTEGER NOT NULL REFERENCES medicines(id),
                cloud_uuid TEXT UNIQUE,
                batch_no TEXT NOT NULL,
                expiry_date TEXT,
                quantity INTEGER NOT NULL CHECK(quantity>=0),
                purchase_price REAL NOT NULL DEFAULT 0 CHECK(purchase_price>=0),
                sale_price REAL NOT NULL DEFAULT 0 CHECK(sale_price>=0),
                source TEXT NOT NULL DEFAULT 'opening',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(medicine_id,batch_no)
            );
            CREATE INDEX idx_batches_medicine_expiry ON medicine_batches(medicine_id,expiry_date);
            CREATE TABLE stock_movements (
                id TEXT PRIMARY KEY,
                transaction_id TEXT NOT NULL,
                medicine_id INTEGER NOT NULL REFERENCES medicines(id),
                batch_id TEXT REFERENCES medicine_batches(id),
                movement_type TEXT NOT NULL,
                quantity_delta INTEGER NOT NULL CHECK(quantity_delta<>0),
                actor_id INTEGER REFERENCES users(id),
                source_event_id TEXT,
                occurred_at TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX idx_stock_movements_medicine_time ON stock_movements(medicine_id,occurred_at);
            CREATE TABLE local_purchases (
                id TEXT PRIMARY KEY,
                transaction_id TEXT NOT NULL UNIQUE,
                supplier_id INTEGER REFERENCES suppliers(id),
                total_amount REAL NOT NULL CHECK(total_amount>=0),
                amount_paid REAL NOT NULL CHECK(amount_paid>=0),
                status TEXT NOT NULL,
                occurred_at TEXT NOT NULL
            );
            CREATE TABLE local_purchase_items (
                id TEXT PRIMARY KEY,
                purchase_id TEXT NOT NULL REFERENCES local_purchases(id),
                medicine_id INTEGER NOT NULL REFERENCES medicines(id),
                batch_id TEXT NOT NULL REFERENCES medicine_batches(id),
                quantity INTEGER NOT NULL CHECK(quantity>0),
                purchase_cost REAL NOT NULL,
                selling_price REAL NOT NULL
            );
            CREATE TABLE local_payments (
                id TEXT PRIMARY KEY,
                transaction_id TEXT NOT NULL UNIQUE,
                sale_id INTEGER REFERENCES sales(id),
                purchase_id TEXT REFERENCES local_purchases(id),
                amount REAL NOT NULL CHECK(amount>=0),
                method TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'recorded',
                occurred_at TEXT NOT NULL,
                CHECK((sale_id IS NOT NULL) <> (purchase_id IS NOT NULL))
            );
            CREATE TABLE sync_outbox (
                event_id TEXT PRIMARY KEY,
                transaction_id TEXT NOT NULL,
                tenant_id TEXT,
                shop_id TEXT,
                device_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                aggregate_id TEXT NOT NULL,
                schema_version INTEGER NOT NULL DEFAULT 1,
                occurred_at TEXT NOT NULL,
                payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending','inflight','acked','failed','conflict')),
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TEXT NOT NULL,
                acknowledged_at TEXT,
                last_error TEXT
            );
            CREATE INDEX idx_outbox_pending ON sync_outbox(status,next_attempt_at,occurred_at);
            CREATE TABLE sync_checkpoints (
                stream TEXT PRIMARY KEY,
                cursor TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE downloaded_changes (
                change_id TEXT PRIMARY KEY,
                stream TEXT NOT NULL,
                version TEXT NOT NULL,
                change_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                downloaded_at TEXT NOT NULL,
                applied_at TEXT
            );
            CREATE TABLE sync_conflicts (
                conflict_id TEXT PRIMARY KEY,
                event_id TEXT,
                conflict_type TEXT NOT NULL,
                details TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','resolved','dismissed')),
                created_at TEXT NOT NULL,
                resolved_at TEXT,
                resolution_note TEXT
            );
            CREATE TABLE offline_auth_cache (
                username TEXT PRIMARY KEY COLLATE NOCASE,
                user_uuid TEXT NOT NULL,
                full_name TEXT NOT NULL,
                role TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                tenant_name TEXT NOT NULL,
                shop_id TEXT NOT NULL,
                shop_name TEXT NOT NULL,
                permissions TEXT NOT NULL,
                last_online_at TEXT NOT NULL,
                authorized_until TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1
            );
            ALTER TABLE sales ADD COLUMN transaction_id TEXT;
            ALTER TABLE sales ADD COLUMN shop_id TEXT;
            ALTER TABLE sales ADD COLUMN device_id TEXT;
            ALTER TABLE sale_items ADD COLUMN batch_id TEXT;
            ALTER TABLE sale_items ADD COLUMN unit_cost REAL NOT NULL DEFAULT 0;
            ALTER TABLE sale_items ADD COLUMN cloud_uuid TEXT;
            ALTER TABLE sale_returns ADD COLUMN transaction_id TEXT;
            ALTER TABLE sale_returns ADD COLUMN batch_id TEXT;
            ALTER TABLE users ADD COLUMN cloud_uuid TEXT;
            ALTER TABLE medicines ADD COLUMN cloud_uuid TEXT;
            ALTER TABLE medicines ADD COLUMN cloud_sku TEXT;
            ALTER TABLE suppliers ADD COLUMN cloud_uuid TEXT;
            ALTER TABLE customers ADD COLUMN cloud_uuid TEXT;
            """
        )
        now = conn.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ','now')").fetchone()[0]
        conn.execute(
            "INSERT INTO sync_identity(singleton,device_id) VALUES (1,?)",
            (str(uuid.uuid4()),),
        )
        medicines = conn.execute("SELECT * FROM medicines ORDER BY id").fetchall()
        for medicine in medicines:
            batch_id = str(uuid.uuid4())
            batch_no = medicine['batch_no'] or f'OPENING-{medicine["id"]}'
            conn.execute(
                "INSERT INTO medicine_batches(id,medicine_id,batch_no,expiry_date,quantity,purchase_price,sale_price,source) "
                "VALUES(?,?,?,?,?,?,?,'opening')",
                (batch_id, medicine['id'], batch_no, medicine['expiry_date'], medicine['quantity'],
                 medicine['purchase_price'], medicine['sale_price']),
            )
            if medicine['quantity']:
                conn.execute(
                    "INSERT INTO stock_movements(id,transaction_id,medicine_id,batch_id,movement_type,quantity_delta,occurred_at,details) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), str(uuid.uuid4()), medicine['id'], batch_id, 'opening',
                     medicine['quantity'], now, '{"source":"legacy SQLite migration"}'),
                )
            conn.execute(
                "UPDATE sale_items SET batch_id=? WHERE medicine_id=? AND batch_id IS NULL",
                (batch_id, medicine["id"]),
            )
        conn.execute("UPDATE sales SET transaction_id=lower(hex(randomblob(16))) WHERE transaction_id IS NULL")
        conn.execute("CREATE UNIQUE INDEX ux_sales_transaction_id ON sales(transaction_id)")
        conn.execute("INSERT INTO schema_migrations(version) VALUES(3)")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
