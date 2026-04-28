from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from .backtest import iter_features, run_backtest, write_jsonl
from .config import StrategyAiSettings
from .engine import StrategyAiEngine
from .llm.factory import build_llm_client
from .llm.fixture import FixtureLLMClient
from .strategy import AiStrategy

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="strategy-ai",
        description="Strategy Engine B (LLM-based) CLI.",
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
        help="StrategySignal JSONL の出力パス。省略時は backtest_output_dir/signals.jsonl。",
    )
    bt.add_argument(
        "--live",
        action="store_true",
        help="実 LLM (Gemini 等) を叩く。未指定なら FixtureLLMClient で決定論的に評価。",
    )
    bt.add_argument(
        "--fixture-responses",
        dest="fixture_responses",
        type=Path,
        default=None,
        help=(
            "FixtureLLMClient に渡す JSON 配列ファイル (string のリスト)。"
            "未指定ならデフォルト BUY/HOLD/SELL ラウンドロビン。"
        ),
    )
    bt.add_argument(
        "--min-interval-seconds",
        dest="min_interval_seconds",
        type=float,
        default=None,
        help=("AiStrategy のレート制御。省略時は backtest 用に 0 (毎 features で評価)。"),
    )
    return p


def _build_strategy(settings: StrategyAiSettings, *, args: argparse.Namespace) -> AiStrategy:
    if args.live:
        llm = build_llm_client(settings)
        min_interval = (
            args.min_interval_seconds
            if args.min_interval_seconds is not None
            else settings.ai_min_interval_seconds
        )
    else:
        if args.fixture_responses is not None:
            payload = json.loads(args.fixture_responses.read_text(encoding="utf-8"))
            if not isinstance(payload, list) or not all(isinstance(x, str) for x in payload):
                raise SystemExit(
                    f"--fixture-responses must be a JSON array of strings: {args.fixture_responses}"
                )
            llm = FixtureLLMClient.from_iterable(payload)
        else:
            llm = FixtureLLMClient()
        min_interval = args.min_interval_seconds if args.min_interval_seconds is not None else 0.0
    return AiStrategy(llm=llm, min_interval_seconds=min_interval)


async def _run_backtest_cmd(args: argparse.Namespace) -> int:
    settings = StrategyAiSettings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    if not args.input_path.exists():
        logger.error("input not found: %s", args.input_path)
        return 2

    strategy = _build_strategy(settings, args=args)
    engine = StrategyAiEngine([strategy])
    summary = await run_backtest(engine, iter_features(args.input_path))

    out_path = args.output_path or (settings.backtest_output_dir / "signals.jsonl")
    write_jsonl(summary.signals, out_path)
    logger.info(
        "backtest done: input=%s output=%s features=%d signals=%d live=%s",
        args.input_path,
        out_path,
        summary.feature_count,
        summary.signal_count,
        args.live,
    )
    return 0


def main() -> None:
    args = _build_parser().parse_args()
    if args.command == "backtest":
        sys.exit(asyncio.run(_run_backtest_cmd(args)))
    raise SystemExit(f"unknown command: {args.command}")


if __name__ == "__main__":
    main()
