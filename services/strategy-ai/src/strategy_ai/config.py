from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class StrategyAiSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    log_level: str = "INFO"

    llm_provider: str = "gemini"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"
    gemini_timeout_seconds: float = 30.0
    local_llm_base_url: str = ""
    local_llm_api_key: str = ""
    local_llm_model: str = ""
    local_llm_timeout_seconds: float = 60.0
    local_llm_max_concurrency: int = 2

    ai_min_interval_seconds: float = 300.0
    ai_temperature: Decimal = Decimal("0.0")
    ai_max_output_tokens: int = 2048
    ai_silence_warn_seconds: float = 3600.0

    supabase_url: str = ""
    supabase_secret_key: str = ""

    pubsub_project_id: str = ""
    pubsub_emulator_host: str = ""
    pubsub_subscription_features: str = "strategy-ai-rule-signals"
    pubsub_topic_signals: str = "strategy-signals-b"
    pubsub_pull_max_messages: int = 5
    pubsub_ack_deadline_seconds: int = 30

    backtest_output_dir: Path = Path("./out/strategy-ai")
