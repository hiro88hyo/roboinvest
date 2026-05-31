from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict
from trade_contracts.enums import TradingStyle

ConflictPolicy = Literal["skip", "prefer_rule", "prefer_ai"]


class AggregatorSettings(BaseSettings):
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
    pubsub_subscription_signals_a: str = "aggregator-strategy-signals-a"
    pubsub_subscription_signals_b: str = "aggregator-strategy-signals-b"
    pubsub_topic_trade_signals: str = "trade-signals"
    pubsub_pull_max_messages: int = 100
    pubsub_ack_deadline_seconds: int = 30

    source_weight_rule: Decimal = Decimal("1.0")
    source_weight_ai: Decimal = Decimal("1.0")
    consensus_min_confidence: Decimal = Decimal("0.3")
    min_confidence_rule_only: Decimal = Decimal("0.5")
    min_confidence_ai_only: Decimal = Decimal("0.5")
    min_confidence_consensus: Decimal = Decimal("0.3")
    conflict_policy: ConflictPolicy = "skip"

    pairing_bucket_ms: int = 1000
    pairing_window_ms: int = 1000

    default_holding_type: TradingStyle = TradingStyle.DAY

    backtest_output_dir: Path = Path("./out/aggregator")
