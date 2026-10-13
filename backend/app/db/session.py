from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


engine = create_engine(get_settings().database_url, pool_pre_ping=True, pool_size=5, max_overflow=10)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def set_tenant_context(db: Session, tenant_id) -> None:
    # Transaction-local PostgreSQL setting, consumed by RLS policies.
    db.execute(text("SELECT set_config('app.tenant_id', :tenant_id, true)"), {"tenant_id": str(tenant_id)})


def set_user_context(db: Session, user_id) -> None:
    db.execute(text("SELECT set_config('app.user_id', :user_id, true)"), {"user_id": str(user_id)})
