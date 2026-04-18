from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .backtest import run_backtest, write_jsonl, write_parquet
from .clients.supabase import SupabaseReader
from .config import FeatureEngineSettings

JST = ZoneInfo("Asia/Tokyo")
OutputFormat = str  # "jsonl" | "parquet" | "both"


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
        description="Feature Engine CLI (Phase 2: backtest).",
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
    return p


async def _run_backtest_cmd(
    end_date: date,
    symbols: list[str] | None,
    output: OutputFormat,
    output_dir: Path | None,
) -> int:
    settings = FeatureEngineSettings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    logger = logging.getLogger(__name__)

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
    raise SystemExit(f"unknown command: {args.command}")


if __name__ == "__main__":
    main()
