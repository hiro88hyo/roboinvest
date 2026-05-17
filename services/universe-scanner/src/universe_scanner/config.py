from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class JQuantsPlan(StrEnum):
    LIGHT = "light"
    STANDARD = "standard"
    PREMIUM = "premium"


class JQuantsApiVersion(StrEnum):
    V1 = "v1"
    V2 = "v2"


class ScannerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    jquants_api_version: JQuantsApiVersion = JQuantsApiVersion.V2
    jquants_api_key: str | None = None
    jquants_refresh_token: str | None = None
    jquants_plan: JQuantsPlan = JQuantsPlan.STANDARD
    jquants_api_base: str = "https://api.jquants.com/v2"

    supabase_url: str
    supabase_secret_key: str

    scan_static_min_turnover_jpy: Decimal = Decimal("100000000")
    scan_static_price_min: Decimal = Decimal("300")
    scan_static_price_max: Decimal = Decimal("20000")
    scan_static_market_segments: tuple[str, ...] = Field(
        default=("プライム", "スタンダード", "グロース")
    )

    scan_dynamic_top_n: int = 30
    scan_lookback_days: int = 60

    scan_weight_volatility: float = 1.0
    scan_weight_volume_surge: float = 1.0
    scan_weight_momentum: float = 1.0

    log_level: str = "INFO"
