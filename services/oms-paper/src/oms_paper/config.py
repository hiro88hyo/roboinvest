"""OMS Paper の設定モデル。

``OmsPaperSettings`` は環境変数 / ``.env`` からロードする BaseSettings。
Phase 1/2 では一部のフィールドのみが読まれ、Phase 3 で Pub/Sub と Supabase
関連の項目を実際に消費する。
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from trade_contracts.enums import TradingStyle


class OmsPaperSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    log_level: str = "INFO"

    supabase_url: str = ""
    supabase_secret_key: str = ""

    pubsub_project_id: str = ""
    pubsub_emulator_host: str = ""
    pubsub_subscription_paper_orders: str = "oms-paper-paper-orders"
    pubsub_subscription_raw_market_data: str = "oms-paper-raw-market-data"
    pubsub_pull_max_messages: int = 100
    raw_book_drain_max_batches: int = 1
    pubsub_ack_deadline_seconds: int = 30
    market_data_stale_warn_seconds: float | None = 180.0
    order_book_max_age_seconds: float | None = 10.0
    paper_day_stop_monitor_enabled: bool = True

    day_closeout_time: str = "14:50"
    day_closeout_timezone: str = "Asia/Tokyo"

    default_holding_type: TradingStyle = TradingStyle.DAY

    backtest_output_dir: Path = Path("./out/oms-paper")
