from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import get_settings

settings = get_settings()
limiter = Limiter(
    key_func=get_remote_address,
    headers_enabled=True,
    storage_uri=settings.rate_limit_storage_uri,
    enabled=settings.app_env != "test",
)
