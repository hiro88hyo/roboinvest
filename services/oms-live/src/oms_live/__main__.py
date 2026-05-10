"""OMS Live CLI entry point.

``stream`` サブコマンド 1 種のみ。``live-orders`` を購読して kabu API へ実発注し、
14:50 JST のデイクローズアウトスケジューラを並走させる。

OMS Paper と異なり ``backtest`` は持たない (本番資金に直結する OMS Live を
バックテストする概念がないため; 検証は oms-paper で行う前提)。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence

from .clients.pubsub import PubSubSubscriber
from .clients.supabase import SupabaseClient, SupabaseError
from .config import OmsLiveSettings
from .kabu_client import KabuLiveClient
from .scheduler import parse_hhmm, run_closeout_scheduler
from .streaming.runner import StreamRunner

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="oms-live",
        description="OMS Live: live-orders -> kabu sendorder -> trades_live / positions / pnl.",
    )
    subparsers = p.add_subparsers(dest="command", required=True)

    st = subparsers.add_parser(
        "stream",
        help="live-orders を購読する常駐ループ + 14:50 closeout",
    )
    st.add_argument(
        "--iterations",
        dest="iterations",
        type=int,
        default=None,
        help="run() / scheduler の反復回数 (デバッグ用)。省略時は無限ループ。",
    )
    st.add_argument(
        "--no-closeout",
        dest="no_closeout",
        action="store_true",
        help="14:50 closeout スケジューラを起動しない (テスト・手動 closeout 時)。",
    )
    return p


async def _run_stream_cmd(*, iterations: int | None, no_closeout: bool) -> int:
    settings = OmsLiveSettings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    if not settings.pubsub_project_id:
        logger.error("PUBSUB_PROJECT_ID must be set for stream mode")
        return 2
    if not settings.supabase_url or not settings.supabase_secret_key:
        logger.error("SUPABASE_URL and SUPABASE_SECRET_KEY must be set for stream mode")
        return 2
    if not settings.kabu_api_password or not settings.kabu_order_password:
        logger.error("KABU_API_PASSWORD and KABU_ORDER_PASSWORD must be set for stream mode")
        return 2

    hour, minute = parse_hhmm(settings.day_closeout_time)

    async with (
        PubSubSubscriber(
            project_id=settings.pubsub_project_id,
            emulator_host=settings.pubsub_emulator_host,
        ) as subscriber,
        SupabaseClient(
            url=settings.supabase_url,
            secret_key=settings.supabase_secret_key,
        ) as supabase,
        KabuLiveClient(
            base_url=settings.kabu_api_base_url,
            api_password=settings.kabu_api_password,
            timeout_seconds=settings.kabu_http_timeout_seconds,
        ) as kabu,
    ):
        runner = StreamRunner(
            subscriber=subscriber,
            supabase=supabase,
            kabu=kabu,
            settings=settings,
        )
        tasks: list[asyncio.Task[object]] = [
            asyncio.create_task(runner.run(iterations=iterations), name="oms-live-stream"),
        ]
        if not no_closeout:
            tasks.append(
                asyncio.create_task(
                    run_closeout_scheduler(runner, hour=hour, minute=minute, iterations=iterations),
                    name="oms-live-closeout-scheduler",
                )
            )
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logger.info("stream cancelled, shutting down")
            for t in tasks:
                t.cancel()
            raise
        except SupabaseError:
            logger.critical(
                "oms-live aborted by fail-fast: supabase write failure. "
                "do NOT auto-restart. reconcile kabu /orders vs trades_live manually, "
                "then start the stream again."
            )
            for t in tasks:
                t.cancel()
            return 3
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "stream":
        return asyncio.run(
            _run_stream_cmd(iterations=args.iterations, no_closeout=args.no_closeout)
        )
    raise SystemExit(f"unknown command: {args.command}")


if __name__ == "__main__":
    sys.exit(main())
