from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

DEFAULT_STRATEGIES = ("rsi_threshold", "bollinger_breakout")


class StrategyRuleSettings(BaseSettings):
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
    pubsub_subscription_features: str = "strategy-rule-processed-features"
    pubsub_topic_signals: str = "strategy-signals-a"
    pubsub_topic_ai_triggers: str = "strategy-ai-triggers"
    pubsub_pull_max_messages: int = 100
    pubsub_ack_deadline_seconds: int = 30
    ai_trigger_min_confidence: float = 0.8

    strategies_enabled: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: list(DEFAULT_STRATEGIES),
    )

    sma_min_gap_ratio: Decimal = Decimal("0.005")
    sma_full_confidence_gap_ratio: Decimal = Decimal("0.02")
    sma_buy_require_price_above_vwap: bool = False
    rsi_buy_threshold: Decimal = Decimal("25")
    rsi_sell_threshold: Decimal = Decimal("75")
    bollinger_breakout_tolerance: Decimal = Decimal("0.15")
    bollinger_buy_require_lower_reclaim: bool = False
    entry_volume_ratio_min: Decimal | None = None
    rsi_buy_require_price_above_vwap: bool = True
    rsi_buy_require_sma_uptrend: bool = True
    bollinger_buy_require_price_above_vwap: bool = True
    bollinger_buy_require_sma_uptrend: bool = True
    entry_max_spread_bps: Decimal | None = None
    entry_max_spread_ticks: Decimal | None = None
    entry_min_ask_depth_5: int | None = None
    entry_min_book_imbalance_5: Decimal | None = None
    entry_min_minutes_from_open: int | None = None
    entry_min_minutes_to_close: int | None = None
    entry_max_book_age_seconds: Decimal | None = None
    entry_max_price: Decimal | None = None
    buy_target_pct: Decimal | None = None
    buy_trailing_stop_pct: Decimal | None = None
    orb_range_minutes: int = 15
    orb_entry_minute: int = 15
    orb_min_minutes_to_close: int = 45
    orb_max_stop_risk_bps: Decimal | None = Decimal("300")
    orb_cooldown_seconds: int = 900
    orb_require_vwap: bool = True
    orb_target_r_multiple: Decimal | None = Decimal("1.5")
    orb_min_breakout_volume_delta: int | None = None
    orb_min_opening_range_volume: int | None = None
    relative_momentum_min_return_from_open_bps: Decimal = Decimal("300")
    relative_momentum_min_peer_percentile: Decimal = Decimal("0.9")
    relative_momentum_min_vwap_distance_bps: Decimal = Decimal("30")
    relative_momentum_min_minutes_from_open: int = 15
    relative_momentum_min_minutes_to_close: int = 45
    relative_momentum_max_stop_risk_bps: Decimal | None = Decimal("200")
    relative_momentum_target_r_multiple: Decimal | None = Decimal("1.5")

    backtest_output_dir: Path = Path("./out/strategy-rule")

    @field_validator("strategies_enabled", mode="before")
    @classmethod
    def _split_strategies(cls, v: Any) -> Any:
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v

    @field_validator(
        "entry_volume_ratio_min",
        "entry_max_spread_bps",
        "entry_max_spread_ticks",
        "entry_min_book_imbalance_5",
        "entry_min_minutes_from_open",
        "entry_min_minutes_to_close",
        "entry_min_ask_depth_5",
        "entry_max_book_age_seconds",
        "entry_max_price",
        "buy_target_pct",
        "buy_trailing_stop_pct",
        "orb_max_stop_risk_bps",
        "orb_target_r_multiple",
        "orb_min_breakout_volume_delta",
        "orb_min_opening_range_volume",
        "relative_momentum_max_stop_risk_bps",
        "relative_momentum_target_r_multiple",
        mode="before",
    )
    @classmethod
    def _empty_entry_filter_to_none(cls, v: Any) -> Any:
        if v == "":
            return None
        return v
