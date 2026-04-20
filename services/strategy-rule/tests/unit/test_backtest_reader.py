from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from strategy_rule.backtest.reader import iter_features
from trade_contracts.features import ProcessedFeatures


def _write(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_round_trip(
    tmp_path: Path,
    features_factory: Callable[..., ProcessedFeatures],
) -> None:
    features = [
        features_factory(symbol="7203", timestamp=datetime(2026, 4, 20, 9, 0, tzinfo=UTC)),
        features_factory(
            symbol="7203",
            timestamp=datetime(2026, 4, 20, 9, 1, tzinfo=UTC),
            sma_short=Decimal("100"),
            sma_long=Decimal("99"),
        ),
    ]
    path = tmp_path / "features.jsonl"
    _write(path, [f.model_dump_json() for f in features])

    out = list(iter_features(path))
    assert len(out) == 2
    assert out[0].symbol == "7203"
    assert out[1].sma_short == Decimal("100")


def test_skips_blank_lines(
    tmp_path: Path,
    features_factory: Callable[..., ProcessedFeatures],
) -> None:
    f = features_factory()
    path = tmp_path / "features.jsonl"
    path.write_text(f"\n{f.model_dump_json()}\n\n", encoding="utf-8")

    out = list(iter_features(path))
    assert len(out) == 1


def test_malformed_row_raises(tmp_path: Path) -> None:
    from pydantic import ValidationError

    path = tmp_path / "features.jsonl"
    path.write_text('{"symbol": "X"}\n', encoding="utf-8")
    with pytest.raises(ValidationError):
        list(iter_features(path))
