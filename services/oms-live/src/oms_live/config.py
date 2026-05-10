"""OMS Live の設定モデル。

``OmsLiveSettings`` は環境変数 / ``.env`` からロードする BaseSettings。
Phase 1 では kabu API 関連のキーのみ実消費し、Phase 2 で Pub/Sub と
Supabase 関連の項目を使う。Phase 3 で安全装備 (max_qty / allowed_symbols /
dry_run) を追加。
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
    kabu_default_exchange: int = 9
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

    # --- Phase 3 safety knobs ---
    # Gateway がリスク検証の主責務だが、本番投入前のセーフティネットとして OMS Live
    # 側でも env で強制できるようにする。デフォルトは無効 (従来挙動と同じ)。
    oms_live_max_qty_per_order: int | None = None
    """1 注文あたりの最大株数。None なら無制限。``order.quantity`` がこの値を超えたら
    sendorder せず safety_rejected として ack。closeout では適用しない (持ち越し
    決済を阻害しない)。"""

    oms_live_allowed_symbols: str = ""
    """カンマ区切りの許可銘柄リスト (例: ``"7203,9984"``)。空文字なら全銘柄許可。
    closeout では適用しない (持ち越し決済のため)。``allowed_symbol_set`` で参照。"""

    oms_live_dry_run: bool = False
    """True なら kabu に sendorder せず log だけ出して ack する。Supabase 書込も
    一切行わない。closeout でも同様に no-op になる。Phase 3 検証時のセーフティ。"""

    @property
    def allowed_symbol_set(self) -> frozenset[str]:
        """``oms_live_allowed_symbols`` をパースした集合。空集合なら全銘柄許可。"""
        return frozenset(s.strip() for s in self.oms_live_allowed_symbols.split(",") if s.strip())
