"""`_build_strategy` の直接ユニットテスト。

CLI を経由しない純関数テスト。fixture / live モードの切り替えと
`--fixture-responses` / `--min-interval-seconds` の挙動を検証する。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from strategy_ai.__main__ import _build_strategy
from strategy_ai.config import StrategyAiSettings
from strategy_ai.llm.fixture import FixtureLLMClient


def test_build_strategy_fixture_default_uses_default_responses() -> None:
    settings = StrategyAiSettings()
    strategy = _build_strategy(
        settings,
        use_fixture=True,
        fixture_responses=None,
        min_interval_seconds=None,
    )
    assert isinstance(strategy.llm, FixtureLLMClient)
    # default = BUY/HOLD/SELL ローテーション
    assert len(strategy.llm.responses) == 3
    # fixture モード default は min_interval=0
    assert strategy.min_interval_seconds == 0.0


def test_build_strategy_fixture_with_responses_file(tmp_path: Path) -> None:
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(
        json.dumps(
            [
                json.dumps({"action": "SELL", "confidence": 0.4, "reasoning": "x"}),
                json.dumps({"action": "BUY", "confidence": 0.4, "reasoning": "y"}),
            ]
        ),
        encoding="utf-8",
    )

    settings = StrategyAiSettings()
    strategy = _build_strategy(
        settings,
        use_fixture=True,
        fixture_responses=fixture_path,
        min_interval_seconds=None,
    )
    assert isinstance(strategy.llm, FixtureLLMClient)
    assert len(strategy.llm.responses) == 2


def test_build_strategy_fixture_responses_must_be_string_list(tmp_path: Path) -> None:
    fixture_path = tmp_path / "bad.json"
    fixture_path.write_text(json.dumps({"not": "a list"}), encoding="utf-8")

    settings = StrategyAiSettings()
    with pytest.raises(SystemExit):
        _build_strategy(
            settings,
            use_fixture=True,
            fixture_responses=fixture_path,
            min_interval_seconds=None,
        )


def test_build_strategy_fixture_min_interval_override() -> None:
    settings = StrategyAiSettings()
    strategy = _build_strategy(
        settings,
        use_fixture=True,
        fixture_responses=None,
        min_interval_seconds=12.5,
    )
    assert strategy.min_interval_seconds == 12.5


def test_build_strategy_live_uses_settings_min_interval() -> None:
    settings = StrategyAiSettings(
        gemini_api_key="dummy-key",
        ai_min_interval_seconds=42.0,
    )
    strategy = _build_strategy(
        settings,
        use_fixture=False,
        fixture_responses=None,
        min_interval_seconds=None,
    )
    # live は settings.ai_min_interval_seconds が default
    assert strategy.min_interval_seconds == 42.0
