from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from feature_engine.__main__ import _build_parser, _periodic_warm_flush
from feature_engine.storage.warm import WarmWriter


def test_stream_subcommand_defaults() -> None:
    parser = _build_parser()
    args = parser.parse_args(["stream"])
    assert args.command == "stream"
    assert args.iterations is None
    assert args.warm_flush_interval == 60.0


def test_stream_subcommand_accepts_overrides() -> None:
    parser = _build_parser()
    args = parser.parse_args(["stream", "--iterations", "3", "--warm-flush-interval", "5"])
    assert args.iterations == 3
    assert args.warm_flush_interval == 5.0


def test_backtest_subcommand_still_works() -> None:
    parser = _build_parser()
    args = parser.parse_args(["backtest", "--date", "2026-04-18", "--symbols", "7203,9984"])
    assert args.command == "backtest"
    assert args.symbols == ["7203", "9984"]


async def test_periodic_warm_flush_calls_flush_each_iteration(tmp_path: Path) -> None:
    flushes: list[int] = []

    class _SpyWarm(WarmWriter):
        def flush(self, *, symbol: str | None = None) -> list[Path]:
            flushes.append(1)
            return super().flush(symbol=symbol)

    writer = _SpyWarm(base_dir=tmp_path, resolution="raw")
    sleeps: list[float] = []

    async def _record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    await _periodic_warm_flush(writer, interval=30.0, iterations=3, sleep=_record_sleep)
    assert sleeps == [30.0, 30.0, 30.0]
    assert len(flushes) == 3


async def test_periodic_warm_flush_continues_on_flush_error(tmp_path: Path) -> None:
    calls: list[int] = []

    class _FailingWarm(WarmWriter):
        def flush(self, *, symbol: str | None = None) -> list[Path]:
            calls.append(1)
            raise RuntimeError("disk full")

    writer = _FailingWarm(base_dir=tmp_path, resolution="raw")

    async def _noop_sleep(_: float) -> None:
        return None

    # 例外で落ちずに iterations 回まわりきる
    await _periodic_warm_flush(writer, interval=1.0, iterations=2, sleep=_noop_sleep)
    assert len(calls) == 2


async def test_periodic_warm_flush_zero_iterations_is_noop(tmp_path: Path) -> None:
    writer = WarmWriter(base_dir=tmp_path, resolution="raw")
    called: list[Any] = []

    async def _sleep(_: float) -> None:
        called.append(1)

    await _periodic_warm_flush(writer, interval=1.0, iterations=0, sleep=_sleep)
    assert called == []


def test_unknown_subcommand_exits() -> None:
    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["nonsense"])
