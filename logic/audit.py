"""Accountability trail for a multi-cashier shop: who deleted/changed what,
and when. Admin-only to view."""
from dataclasses import dataclass

from database.db_manager import get_connection


@dataclass
class AuditEntry:
    id: int
    username: str
    action: str
    details: str
    timestamp: str


def log(user_id: int, username: str, action: str, details: str = "") -> None:
    """Best-effort audit entry -- a logging failure must never block the
    actual operation it's describing, so callers don't need to guard this."""
    try:
        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO audit_log (user_id, username, action, details) VALUES (?, ?, ?, ?)",
                (user_id, username, action, details),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def list_entries(limit: int = 500) -> list[AuditEntry]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        return [
            AuditEntry(
                id=r["id"], username=r["username"] or "(unknown)",
                action=r["action"], details=r["details"] or "", timestamp=r["timestamp"],
            )
            for r in rows
        ]
    finally:
        conn.close()
