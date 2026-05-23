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
    day_closeout_time: str = "14:50"
    day_closeout_timezone: str = "Asia/Tokyo"

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
