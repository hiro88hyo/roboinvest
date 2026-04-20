from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .backtest import iter_features, run_backtest, write_jsonl
from .config import StrategyRuleSettings
from .engine import StrategyEngine
from .registry import build_strategies, registered_names
from .strategies import register_builtin

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="strategy-rule",
        description="Strategy Engine A (rule-based) CLI.",
    )
    subparsers = p.add_subparsers(dest="command", required=True)

    bt = subparsers.add_parser(
        "backtest",
        help="ProcessedFeatures JSONL を読んで StrategySignal JSONL を書き出す",
    )
    bt.add_argument(
        "--input",
        dest="input_path",
        type=Path,
        required=True,
        help="ProcessedFeatures JSONL の入力パス。",
    )
    bt.add_argument(
        "--output",
        dest="output_path",
        type=Path,
        default=None,
        help=("StrategySignal JSONL の出力パス。省略時は backtest_output_dir/signals.jsonl。"),
    )
    return p


def _run_backtest_cmd(*, input_path: Path, output_path: Path | None) -> int:
    settings = StrategyRuleSettings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    register_builtin()
    strategies = build_strategies(settings)
    if not strategies:
        logger.error(
            "no strategies enabled: strategies_enabled=%s registered=%s",
            settings.strategies_enabled,
            registered_names(),
        )
        return 2

    if not input_path.exists():
        logger.error("input not found: %s", input_path)
        return 2

    engine = StrategyEngine(strategies)
    summary = run_backtest(engine, iter_features(input_path))

    out_path = output_path or (settings.backtest_output_dir / "signals.jsonl")
    write_jsonl(summary.signals, out_path)
    logger.info(
        "backtest done: input=%s output=%s features=%d signals=%d",
        input_path,
        out_path,
        summary.feature_count,
        summary.signal_count,
    )
    return 0


def main() -> None:
    args = _build_parser().parse_args()
    if args.command == "backtest":
        sys.exit(_run_backtest_cmd(input_path=args.input_path, output_path=args.output_path))
    raise SystemExit(f"unknown command: {args.command}")


if __name__ == "__main__":
    main()
