from sqlalchemy.orm import Session


def test_wp2_commit_failure_returns_error_and_rolls_back_registration(client, monkeypatch):
    from app.db.session import SessionLocal
    from app.models import Tenant

    def fail_commit(self):
        raise RuntimeError("forced commit failure")

    monkeypatch.setattr(Session, "commit", fail_commit)
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app, raise_server_exceptions=False) as isolated_client:
        response = isolated_client.post("/api/v1/auth/register-tenant", json={
        "tenant_name": "Commit Test Pharmacy", "tenant_slug": "commit-test-pharmacy",
        "shop_name": "Main", "shop_code": "MAIN",
        "owner_email": "commit-test@example.test", "owner_name": "Test Owner",
        "owner_password": "a-strong-test-password",
        })

    assert response.status_code >= 400
    monkeypatch.undo()
    with SessionLocal() as db:
        assert db.query(Tenant).filter_by(slug="commit-test-pharmacy").count() == 0
