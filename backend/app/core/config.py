from functools import lru_cache

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    database_url: str
    jwt_secret: SecretStr
    access_token_minutes: int = 10
    refresh_token_days: int = 14
    invitation_hours: int = 48
    cors_origins: str = ""
    rate_limit_storage_uri: str = "memory://"
    daraja_environment: str = "sandbox"
    daraja_consumer_key: SecretStr | None = None
    daraja_consumer_secret: SecretStr | None = None
    daraja_shortcode: str | None = None
    daraja_passkey: SecretStr | None = None
    daraja_callback_base_url: str | None = None
    daraja_transaction_type: str = "CustomerPayBillOnline"
    daraja_initiator: str | None = None
    daraja_security_credential: SecretStr | None = None
    daraja_party_b: str | None = None
    daraja_result_url: str | None = None
    daraja_timeout_url: str | None = None
    platform_backup_dir: str = "backups/tenant-exports"

    @field_validator("jwt_secret")
    @classmethod
    def validate_jwt_secret(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value().encode()) < 32:
            raise ValueError("JWT_SECRET must contain at least 32 bytes")
        return value

    @field_validator("database_url")
    @classmethod
    def require_postgresql(cls, value: str) -> str:
        if not value.startswith("postgresql+psycopg://"):
            raise ValueError("DATABASE_URL must use postgresql+psycopg://")
        return value

    @field_validator("rate_limit_storage_uri")
    @classmethod
    def require_shared_rate_limit_store_in_production(cls, value: str, info):
        if info.data.get("app_env") == "production" and not value.startswith(("redis://", "rediss://")):
            raise ValueError("Production rate limits require a shared Redis storage URI")
        return value

    @field_validator("daraja_environment")
    @classmethod
    def validate_daraja_environment(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"sandbox", "production"}:
            raise ValueError("DARAJA_ENVIRONMENT must be sandbox or production")
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
