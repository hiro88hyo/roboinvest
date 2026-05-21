from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from .backtest import iter_features, run_backtest, write_jsonl
from .clients.pubsub import PubSubPublisher, PubSubSubscriber
from .clients.supabase import SupabaseWriter
from .config import StrategyAiSettings
from .engine import StrategyAiEngine
from .llm.base import LLMClient
from .llm.factory import build_llm_client
from .llm.fixture import FixtureLLMClient
from .strategy import AiStrategy
from .streaming.runner import StreamRunner

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

    st = subparsers.add_parser(
        "stream",
        help="strong rule triggers を購読して strategy-signals-b に publish する常駐ループ",
    )
    st.add_argument(
        "--iterations",
        dest="iterations",
        type=int,
        default=None,
        help="(dev) N バッチだけ処理して終了する。未指定で無限ループ。",
    )
    st.add_argument(
        "--fixture",
        action="store_true",
        help=(
            "実 LLM の代わりに FixtureLLMClient を使う。e2e 検証で AI 経路を有料 API なしで"
            "回したい時に指定。GEMINI_API_KEY のチェックもスキップする。"
        ),
    )
    st.add_argument(
        "--fixture-responses",
        dest="fixture_responses",
        type=Path,
        default=None,
        help=(
            "FixtureLLMClient に渡す JSON 配列ファイル (string のリスト)。"
            "--fixture と併用。未指定ならデフォルト BUY/HOLD/SELL ラウンドロビン。"
        ),
    )
    st.add_argument(
        "--min-interval-seconds",
        dest="min_interval_seconds",
        type=float,
        default=None,
        help=(
            "AiStrategy のレート制御。fixture モードで連続評価したい時は 0 を渡す。"
            "未指定時は live=settings.ai_min_interval_seconds / fixture=0.0。"
        ),
    )
    return p


def _build_strategy(
    settings: StrategyAiSettings,
    *,
    use_fixture: bool,
    fixture_responses: Path | None,
    min_interval_seconds: float | None,
) -> AiStrategy:
    if not use_fixture:
        llm: LLMClient = build_llm_client(settings)
        min_interval = (
            min_interval_seconds
            if min_interval_seconds is not None
            else settings.ai_min_interval_seconds
        )
    else:
        if fixture_responses is not None:
            payload = json.loads(fixture_responses.read_text(encoding="utf-8"))
            if not isinstance(payload, list) or not all(isinstance(x, str) for x in payload):
                raise SystemExit(
                    f"--fixture-responses must be a JSON array of strings: {fixture_responses}"
                )
            llm = FixtureLLMClient.from_iterable(payload)
        else:
            llm = FixtureLLMClient()
        min_interval = min_interval_seconds if min_interval_seconds is not None else 0.0
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

    strategy = _build_strategy(
        settings,
        use_fixture=not args.live,
        fixture_responses=args.fixture_responses,
        min_interval_seconds=args.min_interval_seconds,
    )
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


async def _run_stream_cmd(args: argparse.Namespace) -> int:
    settings = StrategyAiSettings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    if not settings.supabase_url or not settings.supabase_secret_key:
        logger.error("SUPABASE_URL / SUPABASE_SECRET_KEY が未設定です")
        return 2
    if not settings.pubsub_project_id:
        logger.error("PUBSUB_PROJECT_ID が未設定です")
        return 2
    if not args.fixture and not settings.gemini_api_key:
        logger.error("GEMINI_API_KEY が未設定です (stream は実 LLM 必須。--fixture で迂回可)")
        return 2

    strategy = _build_strategy(
        settings,
        use_fixture=args.fixture,
        fixture_responses=args.fixture_responses,
        min_interval_seconds=args.min_interval_seconds,
    )

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
        engine = StrategyAiEngine([strategy])
        runner = StreamRunner(
            subscriber=subscriber,
            publisher=publisher,
            writer=writer,
            engine=engine,
            settings=settings,
        )
        await runner.run(iterations=args.iterations)

    logger.info(
        "stream done: iterations=%s fixture=%s",
        args.iterations,
        args.fixture,
    )
    return 0


def main() -> None:
    args = _build_parser().parse_args()
    if args.command == "backtest":
        sys.exit(asyncio.run(_run_backtest_cmd(args)))
    if args.command == "stream":
        sys.exit(asyncio.run(_run_stream_cmd(args)))
    raise SystemExit(f"unknown command: {args.command}")


if __name__ == "__main__":
    main()
