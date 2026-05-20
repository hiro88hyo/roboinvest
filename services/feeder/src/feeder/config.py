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

    # WebSocket keepalive ping 設定。kabu/Caddy は pong を返さない疑いがあり
    # (本番疎通 2026-05-07: library default 20/20s で ~80 秒後に
    # ConnectionClosedError 発火) デフォルトでは client-side ping を無効化する。
    # 必要なら env で値を入れて library default や任意の値を有効化できる。
    kabu_ws_ping_interval_seconds: float | None = None
    kabu_ws_ping_timeout_seconds: float | None = None
    # 寄り付きなどで PUSH が密になると application 側の publish 待ちで consumer が
    # 一時的に遅れうるため、受信キューはデフォルトより十分大きく持つ。
    kabu_ws_max_queue: int | None = 2048

    supabase_url: str = ""
    supabase_secret_key: str = ""

    pubsub_project_id: str = ""
    pubsub_emulator_host: str = ""
    pubsub_topic_raw_market_data: str = "raw-market-data"
    pubsub_publish_timeout_seconds: float = 30.0

    watchlist_poll_interval_seconds: float = 60.0
    # WS 受信と Pub/Sub publish を完全直列にしないための上限。
    sink_max_pending_records: int = 64

    reconnect_initial_backoff_sec: float = 1.0
    reconnect_max_backoff_sec: float = 60.0

    # 空文字を指定するとキャッシュ無効 (feeder と oms-live で同じパスを設定する)
    kabu_token_cache_file: str = "/tmp/kabu_token_cache.json"
