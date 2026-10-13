"""Online sign-in, device enrollment, and initial cloud catalogue download."""
import json
import platform
import urllib.error
import urllib.request

from database.db_manager import get_connection
from logic import auth
from logic.sync import apply_cloud_bootstrap


def request_json(base_url: str, path: str, *, method="GET", payload=None, token=None, device=None):
    body = json.dumps(payload, separators=(",", ":")).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if device:
        headers.update({"X-Device-ID": device[0], "X-Device-Secret": device[1]})
    request = urllib.request.Request(base_url.rstrip("/") + path, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            data = json.loads(exc.read() or b"{}")
            detail = data.get("message") or data.get("detail") or "Request failed"
        except Exception:
            detail = "Request failed"
        raise RuntimeError(f"Cloud request failed ({exc.code}): {detail}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RuntimeError("The cloud service is unavailable. Check the network and try again.") from None


def online_login(base_url: str, email: str, password: str) -> tuple[dict, dict]:
    tokens = request_json(base_url, "/api/v1/auth/login", method="POST",
                          payload={"email": email, "password": password})
    workspaces = request_json(base_url, "/api/v1/auth/workspaces", token=tokens["access_token"])
    if not workspaces.get("workspaces"):
        raise RuntimeError("This account has no active tenant or shop access.")
    return tokens, workspaces


def enroll_terminal(base_url: str, token: str, workspace: dict, shop: dict) -> tuple[str, str]:
    import keyring

    conn = get_connection()
    try:
        identity = conn.execute("SELECT * FROM sync_identity WHERE singleton=1").fetchone()
        device_name = identity["device_name"] or platform.node() or "Pharmacy terminal"
        current_id = identity["device_id"]
    finally:
        conn.close()
    saved = keyring.get_password("shani-pharmacy-cloud", current_id)
    if saved:
        return current_id, saved
    if "device:manage" not in workspace["permissions"]:
        raise RuntimeError("This terminal is not registered. Ask a tenant administrator to register it, then sign in again.")
    result = request_json(
        base_url, f"/api/v1/tenants/{workspace['tenant_id']}/devices", method="POST",
        token=token, payload={"shop_id": shop["shop_id"], "name": device_name, "platform": platform.platform()},
    )
    keyring.set_password("shani-pharmacy-cloud", result["id"], result["device_credential"])
    return result["id"], result["device_credential"]


def complete_online_sign_in(base_url: str, email: str, password: str,
                            workspace: dict, shop: dict, tokens=None, identity=None) -> auth.User:
    if tokens is None or identity is None:
        tokens, identity = online_login(base_url, email, password)
    device_id, device_secret = enroll_terminal(base_url, tokens["access_token"], workspace, shop)
    device = (device_id, device_secret)
    snapshot = request_json(
        base_url,
        f"/api/v1/tenants/{workspace['tenant_id']}/shops/{shop['shop_id']}/sync/bootstrap",
        device=device,
    )
    apply_cloud_bootstrap(snapshot)
    conn = get_connection()
    try:
        local_id = conn.execute("SELECT device_id FROM sync_identity WHERE singleton=1").fetchone()[0]
        import keyring

        keyring.set_password("shani-pharmacy-cloud", device_id, device_secret)
        if local_id != device_id:
            keyring.delete_password("shani-pharmacy-cloud", local_id)
        conn.execute(
            "UPDATE sync_identity SET tenant_id=?,tenant_name=?,shop_id=?,shop_name=?,device_id=?,device_name=?,"
            "linked_at=datetime('now'),coordination_mode='cloud_client',cloud_api_url=?,last_error=NULL "
            "WHERE singleton=1",
            (workspace["tenant_id"], workspace["tenant_name"], shop["shop_id"], shop["shop_name"],
             device_id, device_name if (device_name := platform.node()) else "This computer", base_url.rstrip("/")),
        )
        # Scope queued, previously local-only records only after the user has
        # authenticated and selected this exact tenant/shop.
        rows = conn.execute("SELECT event_id,payload FROM sync_outbox WHERE tenant_id IS NULL").fetchall()
        for row in rows:
            event = json.loads(row["payload"])
            event.update({"tenant_id": workspace["tenant_id"], "shop_id": shop["shop_id"], "device_id": device_id})
            conn.execute("UPDATE sync_outbox SET tenant_id=?,shop_id=?,device_id=?,payload=? WHERE event_id=?",
                         (workspace["tenant_id"], shop["shop_id"], device_id,
                          json.dumps(event, separators=(",", ":")), row["event_id"]))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return auth.cache_cloud_user_for_offline(
        username=identity["email"], cloud_user_id=identity["user_id"], full_name=identity["full_name"],
        role=workspace["role"], tenant_id=workspace["tenant_id"], tenant_name=workspace["tenant_name"],
        shop_id=shop["shop_id"], shop_name=shop["shop_name"], permissions=workspace["permissions"],
        password=password,
    )
