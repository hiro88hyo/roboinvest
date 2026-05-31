from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from trade_contracts.logging import configure_logging

from .backtest import iter_features, run_backtest, write_jsonl
from .clients.pubsub import PubSubPublisher, PubSubSubscriber
from .clients.supabase import SupabaseWriter
from .config import StrategyRuleSettings
from .engine import StrategyEngine
from .registry import build_strategies, registered_names
from .strategies import register_builtin
from .streaming.runner import StreamRunner

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

    st = subparsers.add_parser(
        "stream",
        help="processed-features を購読して strategy-signals-a に publish する常駐ループ",
    )
    st.add_argument(
        "--iterations",
        dest="iterations",
        type=int,
        default=None,
        help="(dev) N バッチだけ処理して終了する。未指定で無限ループ。",
    )
    return p


def _run_backtest_cmd(*, input_path: Path, output_path: Path | None) -> int:
    settings = StrategyRuleSettings()
    configure_logging(service="strategy-rule", level=settings.log_level)

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


async def _run_stream_cmd(*, iterations: int | None) -> int:
    settings = StrategyRuleSettings()
    configure_logging(service="strategy-rule", level=settings.log_level)

    register_builtin()
    strategies = build_strategies(settings)
    if not strategies:
        logger.error(
            "no strategies enabled: strategies_enabled=%s registered=%s",
            settings.strategies_enabled,
            registered_names(),
        )
        return 2

    if not settings.supabase_url or not settings.supabase_secret_key:
        logger.error("SUPABASE_URL / SUPABASE_SECRET_KEY が未設定です")
        return 2
    if not settings.pubsub_project_id:
        logger.error("PUBSUB_PROJECT_ID が未設定です")
        return 2

    async with (
        PubSubSubscriber(
            project_id=settings.pubsub_project_id,
            emulator_host=settings.pubsub_emulator_host,
        ) as subscriber,
        PubSubPublisher(
            project_id=settings.pubsub_project_id,
            emulator_host=settings.pubsub_emulator_host,
        ) as publisher,
        SupabaseWriter(
            url=settings.supabase_url,
            secret_key=settings.supabase_secret_key,
        ) as writer,
    ):
        engine = StrategyEngine(strategies)
        runner = StreamRunner(
            subscriber=subscriber,
            publisher=publisher,
            writer=writer,
            engine=engine,
            settings=settings,
        )
        await runner.run(iterations=iterations)

    logger.info("stream done: iterations=%s", iterations)
    return 0


def main() -> None:
    args = _build_parser().parse_args()
    if args.command == "backtest":
        sys.exit(_run_backtest_cmd(input_path=args.input_path, output_path=args.output_path))
    if args.command == "stream":
        sys.exit(asyncio.run(_run_stream_cmd(iterations=args.iterations)))
    raise SystemExit(f"unknown command: {args.command}")


if __name__ == "__main__":
    main()
