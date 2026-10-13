"""Authentication and user-management business logic."""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, List

from database.db_manager import DEFAULT_ADMIN_USERNAME, get_connection
from logic.security import hash_password, verify_password
from logic.settings import get_setting, set_setting
from logic.sync import cache_offline_authorization, offline_authorization_is_valid

VALID_ROLES = ("admin", "cashier")


@dataclass
class User:
    id: int
    username: str
    full_name: str
    role: str
    is_active: bool

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def _row_to_user(row) -> User:
    return User(
        id=row["id"],
        username=row["username"],
        full_name=row["full_name"] or "",
        role=row["role"],
        is_active=bool(row["is_active"]),
    )


def login(username: str, password: str) -> Optional[User]:
    """Return a User on success, or None if credentials are invalid/inactive."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username.strip(),)
        ).fetchone()
        if row is None or not row["is_active"]:
            return None
        if row["cloud_uuid"]:
            cached = conn.execute(
                "SELECT * FROM offline_auth_cache WHERE username=? COLLATE NOCASE AND is_active=1",
                (username.strip(),),
            ).fetchone()
            if not offline_authorization_is_valid(cached):
                return None
            if not verify_password(password, cached["password_hash"]):
                return None
            return _row_to_user(row)
        if not verify_password(password, row["password_hash"]):
            return None
        return _row_to_user(row)
    finally:
        conn.close()


def cache_cloud_user_for_offline(
    *, username: str, cloud_user_id: str, full_name: str, role: str,
    tenant_id: str, tenant_name: str, shop_id: str, shop_name: str,
    permissions: List[str], password: str,
) -> User:
    """Cache credentials after the caller has verified them with the cloud API."""
    username = username.strip()
    if not username or not password:
        raise ValueError("Cloud user and password are required.")
    local_role = "admin" if {"staff:manage", "tenant:manage"}.issubset(set(permissions)) else "cashier"
    local_password_hash = hash_password(password)
    conn = get_connection()
    try:
        existing = conn.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE", (username,)).fetchone()
        if existing and existing["cloud_uuid"] not in (None, cloud_user_id):
            raise ValueError("A different cloud account already uses this local username.")
        if existing and existing["cloud_uuid"] is None:
            raise ValueError("This username belongs to a local account; choose another cloud username.")
        if existing:
            conn.execute(
                "UPDATE users SET password_hash=?,full_name=?,role=?,is_active=1 WHERE id=?",
                (local_password_hash, full_name, local_role, existing["id"]),
            )
            local_id = existing["id"]
        else:
            cur = conn.execute(
                "INSERT INTO users(username,password_hash,full_name,role,is_active,cloud_uuid) VALUES(?,?,?,?,1,?)",
                (username, local_password_hash, full_name, local_role, cloud_user_id),
            )
            local_id = cur.lastrowid
        identity = conn.execute("SELECT * FROM sync_identity WHERE singleton=1").fetchone()
        if identity["tenant_id"] and (identity["tenant_id"] != tenant_id or identity["shop_id"] != shop_id):
            raise ValueError("This local database is already linked to a different tenant or shop.")
        conn.execute(
            "UPDATE sync_identity SET tenant_id=?,tenant_name=?,shop_id=?,shop_name=?,linked_at=? "
            "WHERE singleton=1",
            (tenant_id, tenant_name, shop_id, shop_name, datetime.now(timezone.utc).isoformat()),
        )
        cache_offline_authorization(
            conn, username=username, user_uuid=cloud_user_id, full_name=full_name, role=role,
            password_hash=local_password_hash, tenant_id=tenant_id, tenant_name=tenant_name,
            shop_id=shop_id, shop_name=shop_name, permissions=permissions,
        )
        conn.commit()
        return _row_to_user(conn.execute("SELECT * FROM users WHERE id=?", (local_id,)).fetchone())
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_users() -> list[User]:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM users ORDER BY username").fetchall()
        return [_row_to_user(r) for r in rows]
    finally:
        conn.close()


def create_user(username: str, password: str, full_name: str, role: str) -> User:
    username = username.strip()
    if not username:
        raise ValueError("Username is required.")
    if not password or len(password) < 4:
        raise ValueError("Password must be at least 4 characters.")
    if role not in VALID_ROLES:
        raise ValueError(f"Role must be one of {VALID_ROLES}.")

    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT id FROM users WHERE username = ? COLLATE NOCASE", (username,)
        ).fetchone()
        if existing:
            raise ValueError(f"Username '{username}' already exists.")
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, full_name, role) "
            "VALUES (?, ?, ?, ?)",
            (username, hash_password(password), full_name.strip(), role),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        return _row_to_user(row)
    finally:
        conn.close()


def update_user(
    user_id: int,
    full_name: str,
    role: str,
    is_active: bool,
    new_password: Optional[str] = None,
) -> None:
    if role not in VALID_ROLES:
        raise ValueError(f"Role must be one of {VALID_ROLES}.")

    conn = get_connection()
    try:
        if new_password:
            if len(new_password) < 4:
                raise ValueError("Password must be at least 4 characters.")
            conn.execute(
                "UPDATE users SET full_name=?, role=?, is_active=?, password_hash=? "
                "WHERE id=?",
                (full_name.strip(), role, int(is_active), hash_password(new_password), user_id),
            )
        else:
            conn.execute(
                "UPDATE users SET full_name=?, role=?, is_active=? WHERE id=?",
                (full_name.strip(), role, int(is_active), user_id),
            )
        conn.commit()
    finally:
        conn.close()


def delete_user(user_id: int, current_user_id: int) -> None:
    if user_id == current_user_id:
        raise ValueError("You cannot delete the account you are logged in as.")

    conn = get_connection()
    try:
        remaining_admins = conn.execute(
            "SELECT COUNT(*) AS c FROM users WHERE role='admin' AND id != ?",
            (user_id,),
        ).fetchone()["c"]
        target = conn.execute("SELECT role FROM users WHERE id=?", (user_id,)).fetchone()
        if target and target["role"] == "admin" and remaining_admins == 0:
            raise ValueError("Cannot delete the last remaining admin account.")

        # sales.cashier_id, customer_payments.recorded_by and
        # sale_returns.processed_by all reference users(id) with no ON
        # DELETE clause, so deleting a user who has ever processed a sale,
        # payment or return would otherwise fail with a raw, unhandled
        # sqlite3.IntegrityError (FOREIGN KEY constraint failed) instead of
        # a message the admin can act on.
        in_use = conn.execute(
            "SELECT "
            "(SELECT COUNT(*) FROM sales WHERE cashier_id=?) + "
            "(SELECT COUNT(*) FROM customer_payments WHERE recorded_by=?) + "
            "(SELECT COUNT(*) FROM sale_returns WHERE processed_by=?) AS c",
            (user_id, user_id, user_id),
        ).fetchone()["c"]
        if in_use:
            raise ValueError(
                "Cannot delete: this user has recorded sales, payments, or returns. "
                "Deactivate the account instead (uncheck 'Active' when editing it) "
                "to keep those records intact."
            )

        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        conn.commit()
    finally:
        conn.close()


_RECOVERY_PIN_SETTING_KEY = "recovery_pin_hash"
def has_recovery_pin() -> bool:
    """Whether the shop owner has set their own recovery PIN yet."""
    return bool(get_setting(_RECOVERY_PIN_SETTING_KEY, ""))


def set_recovery_pin(pin: str) -> None:
    pin = pin.strip()
    if len(pin) < 12:
        raise ValueError("Recovery PIN must be at least 12 characters.")
    set_setting(_RECOVERY_PIN_SETTING_KEY, hash_password(pin))


def verify_recovery_pin(pin: str) -> bool:
    """Verify only the recovery PIN explicitly configured by the shop owner."""
    pin = pin.strip()
    if not pin:
        return False
    owner_hash = get_setting(_RECOVERY_PIN_SETTING_KEY, "")
    if owner_hash and verify_password(pin, owner_hash):
        return True
    return bool(owner_hash and verify_password(pin, owner_hash))


def recover_admin_access(new_password: str) -> str:
    """Set a caller-chosen password for the oldest admin after PIN verification.

    The caller must verify the configured recovery PIN before invoking this
    function. No default credential is generated or returned to the screen.
    """
    new_password = new_password.strip()
    if len(new_password) < 12:
        raise ValueError("Recovery password must be at least 12 characters.")
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, username FROM users WHERE role='admin' ORDER BY id LIMIT 1"
        ).fetchone()
        if row is None:
            # No admin account exists at all (shouldn't normally happen) --
            # recreate the default one from scratch.
            conn.execute(
                "INSERT INTO users (username, password_hash, full_name, role, is_active) "
                "VALUES (?, ?, ?, 'admin', 1)",
                (DEFAULT_ADMIN_USERNAME, hash_password(new_password), "Administrator"),
            )
            conn.commit()
            return DEFAULT_ADMIN_USERNAME

        conn.execute(
            "UPDATE users SET password_hash=?, is_active=1, role='admin' WHERE id=?",
            (hash_password(new_password), row["id"]),
        )
        conn.commit()
        return row["username"]
    finally:
        conn.close()
