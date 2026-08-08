"""Run one explicit signal date through the prospective event evidence pipeline."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from universe_scanner.calendar import is_tse_business_day, next_business_day

FINANCIAL_ARCHIVE = Path("out/event-research/financial-summaries-20210628-20260624-clean.jsonl")
OHLCV_ARCHIVE = Path("data/reference/daily_ohlcv_20210625_20260624_bydate.csv")
LEDGER = Path("out/event-forward-evidence/ledger.jsonl")
OUTCOME_LEDGER = Path("out/event-forward-evidence/outcomes.jsonl")
READINESS_REPORT = Path("out/event-forward-evidence/kill-switch-readiness.json")
TOKYO = ZoneInfo("Asia/Tokyo")
ENTRY_CUTOFF_TIME_JST = time(9, 0)


def output_paths(signal_date: date) -> tuple[Path, Path]:
    stem = f"causal-candidates-{signal_date.isoformat()}"
    return (
        Path("out/event-paper-observation") / f"{stem}.json",
        Path("out/event-paper-observation") / f"{stem}.csv",
    )


def commands(signal_date: date) -> list[list[str]]:
    value = signal_date.isoformat()
    output_json, output_csv = output_paths(signal_date)
    python = sys.executable
    # Always append a fresh financial-summary response for this explicit date.
    # A completed same-day fetch can predate the next-calendar-day coverage
    # window and must not cause the causal run to reuse an incomplete snapshot.
    return [
        [
            python,
            "scripts/export-jquants-financial-summaries-jsonl.py",
            "--start-date",
            value,
            "--end-date",
            value,
            "--output",
            str(FINANCIAL_ARCHIVE),
            "--log-every-dates",
            "1",
            "--concurrency",
            "1",
            "--sleep-seconds",
            "0.2",
        ],
        [
            python,
            "scripts/export-jquants-daily-ohlcv-csv.py",
            "--start-date",
            value,
            "--end-date",
            value,
            "--output",
            str(OHLCV_ARCHIVE),
            "--resume",
            "--concurrency",
            "1",
            "--sleep-seconds",
            "0.2",
        ],
        [
            python,
            "scripts/detect-event-cluster-paper-candidates.py",
            "--financial-summary-jsonl",
            str(FINANCIAL_ARCHIVE),
            "--ohlcv",
            str(OHLCV_ARCHIVE),
            "--output-json",
            str(output_json),
            "--output-csv",
            str(output_csv),
            "--signal-date",
            value,
        ],
        [
            python,
            "scripts/record-event-forward-evidence.py",
            "--artifact",
            str(output_json),
            "--ledger",
            str(LEDGER),
        ],
        [
            python,
            "scripts/finalize-event-forward-outcomes.py",
            "--ledger",
            str(LEDGER),
            "--outcomes",
            str(OUTCOME_LEDGER),
            "--ohlcv",
            str(OHLCV_ARCHIVE),
        ],
        [
            python,
            "scripts/report-project-kill-switch-readiness.py",
            "--ledger",
            str(LEDGER),
            "--outcomes",
            str(OUTCOME_LEDGER),
            "--output-json",
            str(READINESS_REPORT),
        ],
    ]


def coverage_window(signal_date: date) -> tuple[datetime, datetime]:
    coverage_start = datetime.combine(
        signal_date + timedelta(days=1),
        time(0, 0),
        tzinfo=TOKYO,
    ).astimezone(UTC)
    entry_cutoff = datetime.combine(
        next_business_day(signal_date),
        ENTRY_CUTOFF_TIME_JST,
        tzinfo=TOKYO,
    ).astimezone(UTC)
    return coverage_start, entry_cutoff


def preflight(signal_date: date, *, now: datetime | None = None) -> None:
    if signal_date < date(2026, 7, 1):
        raise ValueError("forward signal date must be on or after 2026-07-01")
    if not is_tse_business_day(signal_date):
        raise ValueError("signal date must be a TSE business day")
    checked_at = datetime.now(UTC) if now is None else now
    if checked_at.tzinfo is None:
        raise ValueError("preflight time must be timezone-aware")
    coverage_start, entry_cutoff = coverage_window(signal_date)
    checked_at = checked_at.astimezone(UTC)
    if not coverage_start <= checked_at < entry_cutoff:
        raise ValueError(
            "forward evidence must run inside the causal coverage window: "
            f"{coverage_start.isoformat()} <= now < {entry_cutoff.isoformat()}"
        )
    output_json, output_csv = output_paths(signal_date)
    existing = [path for path in (output_json, output_csv) if path.exists()]
    if existing:
        raise FileExistsError(
            "refusing to overwrite forward artifacts: " + ",".join(str(path) for path in existing)
        )
    if not os.getenv("JQUANTS_API_KEY") and not (
        os.getenv("JQUANTS_MAIL_ADDRESS") and os.getenv("JQUANTS_PASSWORD")
    ):
        raise ValueError("J-Quants credentials are not present; run this command through op run")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--signal-date", type=date.fromisoformat, required=True)
    parser.add_argument(
        "--print-only",
        action="store_true",
        help="Print the frozen command sequence without credentials or writes.",
    )
    args = parser.parse_args()
    if args.print_only:
        for command in commands(args.signal_date):
            print(subprocess.list2cmdline(command))
        return 0
    preflight(args.signal_date)
    for command in commands(args.signal_date):
        subprocess.run(command, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
