from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

TickResolution = Literal["raw", "1s", "1m", "5m"]


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

    pubsub_project_id: str = ""
    pubsub_emulator_host: str = ""
    pubsub_subscription_raw: str = "feature-engine-raw-market-data"
    pubsub_topic_features: str = "processed-features"
    pubsub_pull_max_messages: int = 100
    pubsub_ack_deadline_seconds: int = 30
    market_data_stale_warn_seconds: float | None = 180.0

    storage_tick_resolution: TickResolution = "1s"
    storage_warm_dir: Path = Path("./data/warm")
    storage_cold_dir: Path = Path("./data/cold")

    log_level: str = "INFO"
