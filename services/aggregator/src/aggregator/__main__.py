"""Aggregator CLI entry point."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .backtest import iter_signals, run_backtest, write_jsonl
from .config import AggregatorSettings
from .consensus import ConsensusConfig

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="aggregator",
        description="Signal Aggregator (consensus of StrategySignal A/B).",
    )
    subparsers = p.add_subparsers(dest="command", required=True)

    bt = subparsers.add_parser(
        "backtest",
        help="StrategySignal JSONL を読んで UnifiedTradeSignal JSONL を書き出す",
    )
    bt.add_argument(
        "--input-a",
        dest="input_a",
        type=Path,
        required=True,
        help="Strategy A (RULE) の StrategySignal JSONL パス。",
    )
    bt.add_argument(
        "--input-b",
        dest="input_b",
        type=Path,
        default=None,
        help="Strategy B (AI) の StrategySignal JSONL パス。省略可 (A 単独パススルー)。",
    )
    bt.add_argument(
        "--output",
        dest="output_path",
        type=Path,
        default=None,
        help="UnifiedTradeSignal JSONL の出力パス。省略時は backtest_output_dir/unified.jsonl。",
    )
    bt.add_argument(
        "--bucket-ms",
        dest="bucket_ms",
        type=int,
        default=None,
        help="ペアリング bucket ミリ秒。省略時は PAIRING_BUCKET_MS 環境変数。",
    )

    subparsers.add_parser(
        "stream",
        help="(Phase 3) strategy-signals-a/b を購読して trade-signals に publish する常駐ループ",
    )
    return p


def _consensus_config_from(settings: AggregatorSettings) -> ConsensusConfig:
    return ConsensusConfig(
        weight_rule=settings.source_weight_rule,
        weight_ai=settings.source_weight_ai,
        min_confidence=settings.consensus_min_confidence,
        conflict_policy=settings.conflict_policy,
        default_holding_type=settings.default_holding_type,
    )


def _run_backtest_cmd(
    *,
    input_a: Path,
    input_b: Path | None,
    output_path: Path | None,
    bucket_ms: int | None,
) -> int:
    settings = AggregatorSettings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    if not input_a.exists():
        logger.error("input-a not found: %s", input_a)
        return 2
    if input_b is not None and not input_b.exists():
        logger.error("input-b not found: %s", input_b)
        return 2

    signals_a = iter_signals(input_a)
    signals_b = iter_signals(input_b) if input_b is not None else None

    summary = run_backtest(
        signals_a,
        signals_b,
        config=_consensus_config_from(settings),
        bucket_ms=bucket_ms if bucket_ms is not None else settings.pairing_bucket_ms,
    )

    out_path = output_path or (settings.backtest_output_dir / "unified.jsonl")
    write_jsonl(summary.unified, out_path)
    logger.info(
        "backtest done: input-a=%s input-b=%s output=%s inputs=%d buckets=%d unified=%d",
        input_a,
        input_b,
        out_path,
        summary.input_count,
        summary.bucket_count,
        summary.unified_count,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "backtest":
        return _run_backtest_cmd(
            input_a=args.input_a,
            input_b=args.input_b,
            output_path=args.output_path,
            bucket_ms=args.bucket_ms,
        )
    if args.command == "stream":
        print(
            "aggregator 'stream' is not implemented yet (Phase 3).",
            file=sys.stderr,
        )
        return 2
    raise SystemExit(f"unknown command: {args.command}")


if __name__ == "__main__":
    sys.exit(main())
