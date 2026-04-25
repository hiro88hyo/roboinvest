"""Feeder の設定モデル。

Phase 1 では ``kabu_*`` 系のみが実際に消費される。Phase 2/3 で Supabase /
Pub/Sub / 再接続関連の項目を順次有効化する。
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class FeederSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    log_level: str = "INFO"

    kabu_api_base_url: str = "http://localhost:18081/kabusapi"
    kabu_ws_url: str = "ws://localhost:18081/kabusapi/websocket"
    kabu_api_password: str = ""
    kabu_default_exchange: int = 1
    kabu_http_timeout_seconds: float = 10.0

    supabase_url: str = ""
    supabase_secret_key: str = ""

    pubsub_project_id: str = ""
    pubsub_emulator_host: str = ""
    pubsub_topic_raw_market_data: str = "raw-market-data"

    reconnect_initial_backoff_sec: float = 1.0
    reconnect_max_backoff_sec: float = 60.0
