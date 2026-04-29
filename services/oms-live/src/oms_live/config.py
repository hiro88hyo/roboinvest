"""OMS Live の設定モデル。

``OmsLiveSettings`` は環境変数 / ``.env`` からロードする BaseSettings。
Phase 1 では kabu API 関連のキーのみ実消費し、Phase 2 で Pub/Sub と
Supabase 関連の項目を使う。
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class OmsLiveSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    log_level: str = "INFO"

    kabu_api_base_url: str = "http://localhost:18081/kabusapi"
    kabu_api_password: str = ""
    kabu_order_password: str = ""
    kabu_default_exchange: int = 1
    kabu_account_type: int = 4
    kabu_http_timeout_seconds: float = 10.0

    supabase_url: str = ""
    supabase_secret_key: str = ""

    pubsub_project_id: str = ""
    pubsub_emulator_host: str = ""
    pubsub_subscription_live_orders: str = "oms-live-live-orders"
    pubsub_pull_max_messages: int = 100
    pubsub_ack_deadline_seconds: int = 30

    day_closeout_time: str = "14:50"
    day_closeout_timezone: str = "Asia/Tokyo"

    order_fill_poll_interval_seconds: float = 1.0
    order_fill_timeout_seconds: float = 30.0
