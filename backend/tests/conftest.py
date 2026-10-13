import os
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
TEST_URL = os.environ.get("TEST_DATABASE_URL", "")
if TEST_URL:
    os.environ["DATABASE_URL"] = TEST_URL
os.environ.setdefault("JWT_SECRET", "test-only-secret-that-is-at-least-32-bytes-long")
os.environ.setdefault("APP_ENV", "test")

from app.core.config import get_settings  # noqa: E402

get_settings.cache_clear()


def _destructive_test_database_is_configured() -> bool:
    if not TEST_URL.startswith("postgresql+psycopg://"):
        return False
    database_name = TEST_URL.rsplit("/", 1)[-1].split("?", 1)[0]
    return database_name.endswith("_test") or database_name.endswith("_tests")


pytestmark = pytest.mark.skipif(
    not _destructive_test_database_is_configured(),
    reason="Set TEST_DATABASE_URL to a disposable PostgreSQL database ending in _test or _tests.",
)


@pytest.fixture(autouse=True)
def migrated_database():
    if not _destructive_test_database_is_configured():
        pytest.skip("A disposable PostgreSQL test database is not configured.")
    from alembic import command
    from alembic.config import Config

    from app.db.session import engine

    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    yield
    command.downgrade(config, "base")
    engine.dispose()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
