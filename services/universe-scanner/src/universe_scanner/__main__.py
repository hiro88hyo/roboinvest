from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

from trade_contracts.logging import configure_logging

from .config import ScannerSettings
from .pipeline import EmptyWatchlistError, run_pipeline

JST = ZoneInfo("Asia/Tokyo")


def _parse_date(raw: str | None) -> date:
    if raw is None:
        return datetime.now(JST).date()
    return date.fromisoformat(raw)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="universe-scanner",
        description="Build today's watchlist from J-Quants data.",
    )
    p.add_argument(
        "--date",
        dest="target_date",
        type=_parse_date,
        default=None,
        help="対象営業日 (YYYY-MM-DD)。省略時は当日 JST。",
    )
    return p


async def _amain(target_date: date) -> int:
    settings = ScannerSettings()
    configure_logging(service="universe-scanner", level=settings.log_level)
    try:
        result = await run_pipeline(settings=settings, target_date=target_date)
    except EmptyWatchlistError as e:
        logging.getLogger(__name__).error("empty watchlist: %s", e)
        return 2
    logging.getLogger(__name__).info(
        "done: valid_date=%s watchlist_size=%d", result.valid_date, result.watchlist_size
    )
    return 0


def main() -> None:
    args = _build_parser().parse_args()
    target_date = args.target_date or datetime.now(JST).date()
    sys.exit(asyncio.run(_amain(target_date)))


if __name__ == "__main__":
    main()
