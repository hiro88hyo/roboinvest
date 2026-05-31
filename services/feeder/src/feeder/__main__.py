"""Feeder CLI entry point.

Subcommands:
- ``stream``: kabuステーション WebSocket に接続し、watchlist 銘柄の PUSH を
  Pub/Sub ``raw-market-data`` に publish する常駐ループ
- ``replay``: 録画した PUSH JSONL を再生し、Pub/Sub もしくは stdout に流す
  (kabu 接続なしで feature-engine / oms-paper 結合確認に使う)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from trade_contracts.logging import configure_logging

from .clients.pubsub import PubSubPublisher
from .config import FeederSettings
from .streaming.runner import build_publish_sink, run_replay, run_stream
from .streaming.session import MessageSink, stdout_sink_factory

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="feeder",
        description="Feeder: kabuステーション WS PUSH -> raw-market-data publisher.",
    )
    subparsers = p.add_subparsers(dest="command", required=True)

    st = subparsers.add_parser(
        "stream",
        help="kabuステーション WS に接続して raw-market-data に publish する常駐ループ",
    )
    st.add_argument(
        "--iterations",
        dest="iterations",
        type=int,
        default=None,
        help="session.run() の反復回数 (デバッグ用)。省略時は無限ループ。",
    )

    rp = subparsers.add_parser(
        "replay",
        help="録画した PUSH JSONL を再生し、Pub/Sub もしくは stdout に流す",
    )
    rp.add_argument(
        "--input",
        dest="input_path",
        type=Path,
        required=True,
        help="PUSH JSONL のパス (1 行 = 1 PUSH JSON)。",
    )
    rp.add_argument(
        "--sink",
        dest="sink",
        choices=("pubsub", "stdout"),
        default="stdout",
        help="出力先。pubsub は raw-market-data に publish、stdout は JSON Lines を吐く。",
    )
    return p


async def _run_stream_cmd(*, iterations: int | None) -> int:
    settings = FeederSettings()
    configure_logging(service="feeder", level=settings.log_level)
    try:
        results = await run_stream(settings, iterations=iterations)
    except RuntimeError as exc:
        logger.error("stream config error: %s", exc)
        return 2
    logger.info("stream finished: cycles=%d", len(results))
    return 0


async def _run_replay_cmd(*, input_path: Path, sink_kind: str) -> int:
    settings = FeederSettings()
    configure_logging(service="feeder", level=settings.log_level)
    if not input_path.exists():
        logger.error("replay input not found: %s", input_path)
        return 2

    if sink_kind == "stdout":
        sink: MessageSink = stdout_sink_factory()
        with input_path.open("r", encoding="utf-8") as f:
            stats = await run_replay(f, sink)
    else:
        if not settings.pubsub_project_id:
            logger.error("PUBSUB_PROJECT_ID must be set for replay --sink pubsub")
            return 2
        async with PubSubPublisher(
            project_id=settings.pubsub_project_id,
            emulator_host=settings.pubsub_emulator_host,
            timeout_seconds=settings.pubsub_publish_timeout_seconds,
        ) as publisher:
            sink = build_publish_sink(publisher, topic=settings.pubsub_topic_raw_market_data)
            with input_path.open("r", encoding="utf-8") as f:
                stats = await run_replay(f, sink)

    logger.info(
        "replay done: lines=%d parsed=%d emitted=%d parse_errors=%d",
        stats.lines_read,
        stats.payloads_parsed,
        stats.records_emitted,
        stats.parse_errors,
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "stream":
        return asyncio.run(_run_stream_cmd(iterations=args.iterations))
    if args.command == "replay":
        return asyncio.run(_run_replay_cmd(input_path=args.input_path, sink_kind=args.sink))
    raise SystemExit(f"unknown command: {args.command}")


if __name__ == "__main__":
    sys.exit(main())
