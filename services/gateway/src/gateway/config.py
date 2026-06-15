"""Gateway の設定モデル。

``GatewaySettings`` は環境変数 / ``.env`` からロードする BaseSettings。
``RiskConfig`` は Phase 1 の純関数が受け取る不変データクラス。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class GatewaySettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    log_level: str = "INFO"

    supabase_url: str = ""
    supabase_secret_key: str = ""

    kabu_api_base_url: str = "http://localhost:18081/kabusapi"
    kabu_api_password: str = ""
    kabu_http_timeout_seconds: float = 10.0
    kabu_token_cache_file: str = "/tmp/kabu_token_cache.json"

    pubsub_project_id: str = ""
    pubsub_emulator_host: str = ""
    pubsub_subscription_trade_signals: str = "gateway-trade-signals"
    pubsub_topic_live_orders: str = "live-orders"
    pubsub_topic_paper_orders: str = "paper-orders"
    pubsub_pull_max_messages: int = 100
    pubsub_ack_deadline_seconds: int = 30

    capital: Decimal = Decimal("1000000")
    max_risk_per_trade_pct: Decimal = Decimal("0.02")
    swing_risk_scale: Decimal = Decimal("0.5")
    default_stop_loss_spread_pct: Decimal = Decimal("0.02")
    min_lot_size: int = 100
    oms_live_max_qty_per_order: int | None = Field(
        default=None,
        validation_alias="OMS_LIVE_MAX_QTY_PER_ORDER",
    )
    live_signal_max_age_seconds: float | None = 300.0
    live_symbol_order_cooldown_seconds: float = 15.0
    day_same_symbol_reentry_block_enabled: bool = True
    market_regime_gateway_log_only_enabled: bool = True
    market_regime_gateway_guard_enabled: bool = False
    soft_loss_throttle_log_only_enabled: bool = True
    soft_loss_throttle_guard_enabled: bool = False
    soft_loss_limit_jpy: Decimal = Decimal("20000")
    liquidity_sizing_enabled: bool = True
    liquidity_thin_daily_volume: int = 50000
    liquidity_thin_daily_turnover_jpy: Decimal = Decimal("50000000")
    liquidity_thin_max_qty_per_order: int = 100
    liquidity_missing_daily_max_qty_per_order: int = 100
    liquidity_max_daily_volume_participation_pct: Decimal = Decimal("0.01")
    live_day_new_buy_start_time: str = "09:15"
    live_day_new_buy_cutoff_time: str = "14:30"
    day_closeout_time: str = "14:50"
    day_closeout_timezone: str = "Asia/Tokyo"
    order_archive_dir: Path = Path("./data/orders")

    backtest_output_dir: Path = Path("./out/gateway")


@dataclass(frozen=True, slots=True)
class RiskConfig:
    """純関数(lot_calculator / validator)が受け取るリスクパラメータ。"""

    capital: Decimal
    max_risk_per_trade_pct: Decimal = Decimal("0.02")
    swing_risk_scale: Decimal = Decimal("0.5")
    default_stop_loss_spread_pct: Decimal = Decimal("0.02")
    min_lot_size: int = 100

    @classmethod
    def from_settings(cls, settings: GatewaySettings) -> RiskConfig:
        return cls(
            capital=settings.capital,
            max_risk_per_trade_pct=settings.max_risk_per_trade_pct,
            swing_risk_scale=settings.swing_risk_scale,
            default_stop_loss_spread_pct=settings.default_stop_loss_spread_pct,
            min_lot_size=settings.min_lot_size,
        )
