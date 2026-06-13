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
    rsi_buy_threshold: Decimal = Decimal("25")
    rsi_sell_threshold: Decimal = Decimal("75")
    bollinger_breakout_tolerance: Decimal = Decimal("0.15")

    backtest_output_dir: Path = Path("./out/strategy-rule")

    @field_validator("strategies_enabled", mode="before")
    @classmethod
    def _split_strategies(cls, v: Any) -> Any:
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v
