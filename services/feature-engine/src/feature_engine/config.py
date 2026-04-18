from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class FeatureEngineSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    supabase_url: str = ""
    supabase_secret_key: str = ""

    indicator_sma_short_window: int = 5
    indicator_sma_long_window: int = 25
    indicator_rsi_period: int = 14
    indicator_vwap_window: int = 20
    indicator_bollinger_period: int = 20
    indicator_bollinger_stddev: float = 2.0

    backtest_lookback_days: int = 60
    backtest_output_dir: Path = Path("./out/feature-engine")

    log_level: str = "INFO"
