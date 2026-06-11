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

    # Separate Supabase project that owns customer-facing data (analysis posts).
    SUPABASE_CUSTOMERS_URL: str = Field(..., min_length=1)
    SUPABASE_CUSTOMERS_SERVICE_ROLE_KEY: str = Field(..., min_length=1)

    METAAPI_TOKEN: str = Field(..., min_length=1)
    METAAPI_MASTER_ACCOUNT_ID: str = Field(..., min_length=1)

    APP_ENV: Literal["production", "staging", "development"] = "production"
    PORT: int = 8000
    TIMEZONE: str = "Asia/Bangkok"
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # Phase 2.5a runtime config
    dry_run: bool = Field(default=True)
    tick_interval_seconds: int = Field(default=60)
    primary_customer_id: str = Field(default="b1798c54-2665-4b85-9a2b-25f07231f8b0")
    gold_ai_symbol: str = Field(default="XAUUSD")
    multi_cfd_ai_symbols: list[str] = Field(
        default_factory=lambda: ["EURUSD", "GBPUSD", "USDJPY", "US500", "NAS100", "GER40"]
    )
    enable_gold_ai: bool = Field(default=True)
    enable_multi_cfd_ai: bool = Field(default=True)
    intent_buffer_size: int = Field(default=100)

    # Phase 2.6 — Telegram notifications
    telegram_enabled: bool = Field(default=False)
    telegram_bot_token: str = Field(default="")
    telegram_chat_id: str = Field(default="")

    # Phase 6 — freeze/unfreeze
    admin_key: str = Field(default="")
    freeze_cache_ttl_seconds: float = Field(default=30.0)

    # Phase 2.6.2 — broker stop-distance padding
    stop_safety_buffer_points: int = Field(default=10)
    min_padded_rr: float = Field(default=1.2)
    symbol_spec_cache_ttl_seconds: float = Field(default=300.0)

    # Phase 2.6.3 — signal duplication guard. After any open for a
    # (product, symbol), suppress new opens for that pair for this many
    # seconds. Default 300s; override via env SIGNAL_COOLDOWN_SECONDS.
    signal_cooldown_seconds: float = Field(default=300.0)

    # Aurum Sniper webhook (TradingView / Pine Script alert → analysis post).
    # Persisted to the separate customers project; Telegram reuses telegram_*.
    AURUM_SNIPER_WEBHOOK_SECRET: str = Field(default="")
    ANALYSIS_TABLE: str = Field(default="analysis_posts")

    # Phase 5a — chart-img.com snapshot. When unset, the snapshot pipeline is
    # skipped gracefully (webhook persist + broadcast + Telegram still run).
    CHARTIMG_API_KEY: str = Field(default="")
    TV_LAYOUT_ID: str = Field(default="")


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    global _settings
    _settings = None
