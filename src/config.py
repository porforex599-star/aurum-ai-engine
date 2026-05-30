from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    SUPABASE_URL: str = Field(..., min_length=1)
    SUPABASE_SERVICE_ROLE_KEY: str = Field(..., min_length=1)

    METAAPI_TOKEN: str = Field(..., min_length=1)
    METAAPI_MASTER_ACCOUNT_ID: str = Field(..., min_length=1)

    APP_ENV: Literal["production", "staging", "development"] = "production"
    PORT: int = 8000
    TIMEZONE: str = "Asia/Bangkok"
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
