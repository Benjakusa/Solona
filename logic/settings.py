"""App-wide configurable settings (key/value store)."""
from database.db_manager import get_connection


def get_setting(key: str, default: str = "") -> str:
    conn = get_connection()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default
    finally:
        conn.close()


def set_setting(key: str, value: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()


def get_low_stock_threshold() -> int:
    return int(get_setting("low_stock_threshold", "10"))


def get_expiry_alert_days() -> int:
    return int(get_setting("expiry_alert_days", "30"))


RECEIPT_FORMATS = {"A5": "A5 (full page)", "58mm": "58mm thermal", "80mm": "80mm thermal"}


def get_receipt_format() -> str:
    value = get_setting("receipt_format", "A5")
    return value if value in RECEIPT_FORMATS else "A5"


def get_shop_name() -> str:
    value = get_setting("shop_name", "SOLONA Pharmacy")
    # Existing installs may have persisted the former product default.
    if value.strip().casefold() in {
        "shani pharmacy management system",
        "shani pharmacy management system (spms)",
    }:
        return "SOLONA Pharmacy"
    return value
