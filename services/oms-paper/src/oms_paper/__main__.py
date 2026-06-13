"""OMS Paper CLI entry point.

Phase 2/3 で ``backtest`` と ``stream`` サブコマンドを提供する。``stream`` は
paper-orders と raw-market-data を購読しながら 14:50 JST のデイクローズアウト
スケジューラを並走させる。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from trade_contracts.enums import TradingStyle
from trade_contracts.logging import configure_logging

from .backtest import (
    build_backtest_report,
    iter_order_books,
    iter_order_requests,
    read_positions_json,
    run_backtest,
    write_backtest_report,
    write_jsonl,
    write_positions_json,
)
from .clients.pubsub import PubSubSubscriber
from .clients.supabase import SupabaseClient
from .config import OmsPaperSettings
from .scheduler import parse_hhmm, run_closeout_scheduler
from .streaming.runner import StreamRunner

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="oms-paper",
        description="OMS Paper: paper-orders + raw-market-data -> simulated fills.",
    )
    subparsers = p.add_subparsers(dest="command", required=True)

    bt = subparsers.add_parser(
        "backtest",
        help="OrderRequest JSONL と OrderBookSnapshot JSONL から擬似約定を再生する",
    )
    bt.add_argument(
        "--orders",
        dest="orders_path",
        type=Path,
        required=True,
        help="OrderRequest JSONL のパス (gateway backtest の --output-approved)。",
    )
    bt.add_argument(
        "--books",
        dest="books_path",
        type=Path,
        required=True,
        help="OrderBookSnapshot JSONL のパス。",
    )
    bt.add_argument(
        "--positions",
        dest="positions_path",
        type=Path,
        default=None,
        help="初期 positions JSON ({symbol: PaperPosition})。省略時は空。",
    )
    bt.add_argument(
        "--output-fills",
        dest="output_fills",
        type=Path,
        required=True,
        help="擬似約定 JSONL (PaperFillRecord) の出力先。",
    )
    bt.add_argument(
        "--output-positions",
        dest="output_positions",
        type=Path,
        required=True,
        help="終了時 positions JSON の出力先。",
    )
    bt.add_argument(
        "--output-rejected",
        dest="output_rejected",
        type=Path,
        default=None,
        help="不約定ログ JSONL (NoFillRecord) の出力先。省略時は書き出さない。",
    )
    bt.add_argument(
        "--output-report",
        dest="output_report",
        type=Path,
        default=None,
        help=(
            "収益指標 JSON (BacktestReport) の出力先。省略時は output-positions と"
            "同じディレクトリの backtest_report.json。"
        ),
    )
    bt.add_argument(
        "--default-holding-type",
        dest="default_holding_type",
        type=TradingStyle,
        choices=list(TradingStyle),
        default=None,
        help="OrderRequest が holding_type を持たないため、新規 BUY 時に使う値。"
        "未指定時は環境変数 DEFAULT_HOLDING_TYPE (デフォルト day)。",
    )

    st = subparsers.add_parser(
        "stream",
        help="paper-orders と raw-market-data を購読する常駐ループ + 14:50 closeout",
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


def _run_backtest_cmd(
    *,
    orders_path: Path,
    books_path: Path,
    positions_path: Path | None,
    output_fills: Path,
    output_positions: Path,
    output_rejected: Path | None,
    output_report: Path | None,
    default_holding_type: TradingStyle | None,
) -> int:
    settings = OmsPaperSettings()
    configure_logging(service="oms-paper", level=settings.log_level)

    if not orders_path.exists():
        logger.error("orders not found: %s", orders_path)
        return 2
    if not books_path.exists():
        logger.error("books not found: %s", books_path)
        return 2
    if positions_path is not None and not positions_path.exists():
        logger.error("positions file not found: %s", positions_path)
        return 2

    initial_positions = read_positions_json(positions_path) if positions_path else {}
    holding_type = default_holding_type or settings.default_holding_type

    summary = run_backtest(
        orders=iter_order_requests(orders_path),
        books=iter_order_books(books_path),
        initial_positions=initial_positions,
        default_holding_type=holding_type,
    )

    write_jsonl(summary.fills, output_fills)
    write_positions_json(summary.final_positions, output_positions)
    if output_rejected is not None:
        write_jsonl(summary.no_fills, output_rejected)
    report_path = output_report or (output_positions.parent / "backtest_report.json")
    write_backtest_report(
        build_backtest_report(summary.closed_trades, summary.execution_quality),
        report_path,
    )

    logger.info(
        "backtest done: orders=%d fills=%d (%s) no_fills=%d (%s) "
        "positions=%s report=%s realized_pnl=%s",
        summary.order_count,
        summary.fill_count,
        output_fills,
        summary.no_fill_count,
        output_rejected if output_rejected is not None else "<suppressed>",
        output_positions,
        report_path,
        summary.realized_pnl,
    )
    return 0


async def _run_stream_cmd(*, iterations: int | None, no_closeout: bool) -> int:
    settings = OmsPaperSettings()
    configure_logging(service="oms-paper", level=settings.log_level)
    if not settings.pubsub_project_id:
        logger.error("PUBSUB_PROJECT_ID must be set for stream mode")
        return 2
    if not settings.supabase_url or not settings.supabase_secret_key:
        logger.error("SUPABASE_URL and SUPABASE_SECRET_KEY must be set for stream mode")
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
    ):
        runner = StreamRunner(
            subscriber=subscriber,
            supabase=supabase,
            settings=settings,
        )
        tasks: list[asyncio.Task[object]] = [
            asyncio.create_task(runner.run(iterations=iterations), name="oms-paper-stream"),
        ]
        if not no_closeout:
            tasks.append(
                asyncio.create_task(
                    run_closeout_scheduler(runner, hour=hour, minute=minute, iterations=iterations),
                    name="oms-paper-closeout-scheduler",
                )
            )
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logger.info("stream cancelled, shutting down")
            for t in tasks:
                t.cancel()
            raise
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "backtest":
        return _run_backtest_cmd(
            orders_path=args.orders_path,
            books_path=args.books_path,
            positions_path=args.positions_path,
            output_fills=args.output_fills,
            output_positions=args.output_positions,
            output_rejected=args.output_rejected,
            output_report=args.output_report,
            default_holding_type=args.default_holding_type,
        )
    if args.command == "stream":
        return asyncio.run(
            _run_stream_cmd(iterations=args.iterations, no_closeout=args.no_closeout)
        )
    raise SystemExit(f"unknown command: {args.command}")


if __name__ == "__main__":
    sys.exit(main())
