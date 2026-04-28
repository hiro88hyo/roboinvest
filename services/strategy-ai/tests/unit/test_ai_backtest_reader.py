from __future__ import annotations

from pathlib import Path

from strategy_ai._testing import make_features
from strategy_ai.backtest.reader import iter_features


def test_iter_features_round_trip(tmp_path: Path) -> None:
    f1 = make_features(symbol="7203")
    f2 = make_features(symbol="9984")
    path = tmp_path / "features.jsonl"
    path.write_text(
        f1.model_dump_json() + "\n\n" + f2.model_dump_json() + "\n",
        encoding="utf-8",
    )

    rows = list(iter_features(path))

    assert len(rows) == 2
    assert rows[0].symbol == "7203"
    assert rows[1].symbol == "9984"


def test_iter_features_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")
    assert list(iter_features(path)) == []
