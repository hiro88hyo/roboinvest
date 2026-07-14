"""Run one explicit signal date through the prospective event evidence pipeline."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

from universe_scanner.calendar import is_tse_business_day

FINANCIAL_ARCHIVE = Path("out/event-research/financial-summaries-20210628-20260624-clean.jsonl")
OHLCV_ARCHIVE = Path("data/reference/daily_ohlcv_20210625_20260624_bydate.csv")
LEDGER = Path("out/event-forward-evidence/ledger.jsonl")


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
            "--resume",
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
    ]


def preflight(signal_date: date) -> None:
    if signal_date < date(2026, 7, 1):
        raise ValueError("forward signal date must be on or after 2026-07-01")
    if not is_tse_business_day(signal_date):
        raise ValueError("signal date must be a TSE business day")
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
