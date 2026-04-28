from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from strategy_ai._testing import make_features
from strategy_ai.prompt import build_prompt


def test_build_prompt_is_deterministic() -> None:
    f = make_features(
        timestamp=datetime(2026, 4, 27, 9, 0, tzinfo=UTC),
        sma_short=Decimal("1010"),
        sma_long=Decimal("1000"),
        rsi=Decimal("55"),
    )
    assert build_prompt(f) == build_prompt(f)


def test_build_prompt_includes_core_fields() -> None:
    f = make_features(
        symbol="9984",
        price=Decimal("8500"),
        sma_short=Decimal("8480"),
        sma_long=Decimal("8400"),
        rsi=Decimal("65.5"),
        vwap=Decimal("8475"),
        bollinger_upper=Decimal("8600"),
        bollinger_middle=Decimal("8500"),
        bollinger_lower=Decimal("8400"),
    )
    prompt = build_prompt(f)
    for needle in (
        "9984",
        "8500",
        "8480",
        "8400",
        "65.5",
        "8475",
        "8600",
        "JSON",
    ):
        assert needle in prompt


def test_build_prompt_handles_missing_indicators() -> None:
    f = make_features(include_book=False)
    prompt = build_prompt(f)
    assert "n/a" in prompt
    assert "no order book" in prompt


def test_build_prompt_renders_top_of_book() -> None:
    f = make_features(
        bids=[(Decimal("999"), 100), (Decimal("998"), 200), (Decimal("997"), 300)],
        asks=[(Decimal("1001"), 100), (Decimal("1002"), 200), (Decimal("1003"), 300)],
    )
    prompt = build_prompt(f)
    assert "ASK 1001" in prompt
    assert "ASK 1003" in prompt
    assert "BID 999" in prompt
    assert "BID 997" in prompt
