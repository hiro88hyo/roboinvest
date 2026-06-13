from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import sys
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from trade_contracts.logging import configure_logging

from .backtest import run_backtest, write_jsonl, write_parquet
from .clients.pubsub import PubSubPublisher, PubSubSubscriber
from .clients.supabase import SupabaseReader, SupabaseWriter
from .config import FeatureEngineSettings
from .scheduler import run_pnl_reset_scheduler
from .storage.book import BookWarmWriter
from .storage.warm import WarmWriter
from .streaming.feature_state import StreamingFeatureState
from .streaming.runner import StreamRunner
from .streaming.session import TickSession

JST = ZoneInfo("Asia/Tokyo")
OutputFormat = str  # "jsonl" | "parquet" | "both"

logger = logging.getLogger(__name__)


class FlushableWriter(Protocol):
    def flush(self) -> list[Path]: ...


def _parse_date(raw: str | None) -> date:
    if raw is None:
        return datetime.now(JST).date()
    return date.fromisoformat(raw)


def _parse_symbols(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    return [s.strip() for s in raw.split(",") if s.strip()]


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="feature-engine",
        description="Feature Engine CLI (backtest / stream).",
    )
    subparsers = p.add_subparsers(dest="command", required=True)

    bt = subparsers.add_parser("backtest", help="daily_ohlcv から ProcessedFeatures を生成する")
    bt.add_argument(
        "--date",
        dest="end_date",
        type=_parse_date,
        default=None,
        help="終端日 (YYYY-MM-DD)。省略時は当日 JST。",
    )
    bt.add_argument(
        "--symbols",
        dest="symbols",
        type=_parse_symbols,
        default=None,
        help="対象銘柄をカンマ区切りで指定 (例: 7203,9984)。省略時は watchlist から解決。",
    )
    bt.add_argument(
        "--output",
        dest="output",
        choices=("jsonl", "parquet", "both"),
        default="jsonl",
        help="出力フォーマット。",
    )
    bt.add_argument(
        "--output-dir",
        dest="output_dir",
        type=Path,
        default=None,
        help="出力先ディレクトリ。省略時は settings.backtest_output_dir。",
    )

    st = subparsers.add_parser(
        "stream",
        help="raw-market-data を購読して processed-features に publish する常駐ループ",
    )
    st.add_argument(
        "--iterations",
        dest="iterations",
        type=int,
        default=None,
        help="(dev) N バッチだけ処理して終了する。未指定で無限ループ。",
    )
    st.add_argument(
        "--warm-flush-interval",
        dest="warm_flush_interval",
        type=float,
        default=60.0,
        help="WarmWriter を定期フラッシュする間隔 (秒)。",
    )
    return p


async def _run_backtest_cmd(
    end_date: date,
    symbols: list[str] | None,
    output: OutputFormat,
    output_dir: Path | None,
) -> int:
    settings = FeatureEngineSettings()
    configure_logging(service="feature-engine", level=settings.log_level)

    if not settings.supabase_url or not settings.supabase_secret_key:
        logger.error("SUPABASE_URL / SUPABASE_SECRET_KEY が未設定です")
        return 2

    out_dir = output_dir or settings.backtest_output_dir

    async with SupabaseReader(
        url=settings.supabase_url,
        secret_key=settings.supabase_secret_key,
    ) as reader:
        result = await run_backtest(
            reader,
            settings,
            end_date=end_date,
            symbols=symbols,
        )
        if not result.features:
            logger.warning("no features produced: end_date=%s", end_date)
            return 0

        stem = f"features_{end_date.isoformat()}"
        if output in ("jsonl", "both"):
            write_jsonl(result.features, out_dir / f"{stem}.jsonl")
        if output in ("parquet", "both"):
            write_parquet(result.dataframe, out_dir / f"{stem}.parquet")

    logger.info(
        "backtest done: end_date=%s symbols=%d features=%d",
        end_date,
        len(result.symbols),
        len(result.features),
    )
    return 0


async def _periodic_warm_flush(
    writer: FlushableWriter,
    *,
    interval: float,
    iterations: int | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """`interval` 秒ごとに `WarmWriter.flush` を呼ぶ常駐タスク。

    flush で例外が出てもログに落としてループは続行する (at-least-once 的な挙動)。
    `iterations` を指定するとテスト用に有限回で終了する。
    """
    count = 0
    while iterations is None or count < iterations:
        await sleep(interval)
        try:
            writer.flush()
        except Exception:
            logger.exception("periodic warm flush failed")
        count += 1


async def _run_stream_cmd(
    *,
    iterations: int | None,
    warm_flush_interval: float,
) -> int:
    settings = FeatureEngineSettings()
    configure_logging(service="feature-engine", level=settings.log_level)

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
        SupabaseReader(
            url=settings.supabase_url,
            secret_key=settings.supabase_secret_key,
        ) as reader,
        SupabaseWriter(
            url=settings.supabase_url,
            secret_key=settings.supabase_secret_key,
        ) as writer,
    ):
        warm_writer = WarmWriter(
            base_dir=settings.storage_warm_dir,
            resolution=settings.storage_tick_resolution,
        )
        book_writer = BookWarmWriter(base_dir=settings.storage_book_dir)
        runner = StreamRunner(
            subscriber=subscriber,
            publisher=publisher,
            reader=reader,
            writer=writer,
            feature_state=StreamingFeatureState.from_settings(settings),
            tick_session=TickSession(),
            settings=settings,
            warm_writer=warm_writer,
            book_writer=book_writer,
        )

        scheduler_task = asyncio.create_task(
            run_pnl_reset_scheduler(writer),
            name="pnl-reset-scheduler",
        )
        flush_task = asyncio.create_task(
            _periodic_warm_flush(warm_writer, interval=warm_flush_interval),
            name="warm-flush",
        )
        book_flush_task = asyncio.create_task(
            _periodic_warm_flush(book_writer, interval=warm_flush_interval),
            name="book-warm-flush",
        )

        try:
            await runner.run(iterations=iterations)
        finally:
            for task in (scheduler_task, flush_task, book_flush_task):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            warm_writer.flush()
            book_writer.flush()

    logger.info("stream done: iterations=%s", iterations)
    return 0


def main() -> None:
    args = _build_parser().parse_args()
    if args.command == "backtest":
        sys.exit(
            asyncio.run(
                _run_backtest_cmd(
                    end_date=args.end_date or datetime.now(JST).date(),
                    symbols=args.symbols,
                    output=args.output,
                    output_dir=args.output_dir,
                )
            )
        )
    if args.command == "stream":
        sys.exit(
            asyncio.run(
                _run_stream_cmd(
                    iterations=args.iterations,
                    warm_flush_interval=args.warm_flush_interval,
                )
            )
        )
    raise SystemExit(f"unknown command: {args.command}")


if __name__ == "__main__":
    main()
