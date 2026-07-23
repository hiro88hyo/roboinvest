#!/usr/bin/env python3
"""Run the previous JST business day's shadow-forward evidence exactly once."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from universe_scanner.calendar import is_tse_business_day

TOKYO = ZoneInfo("Asia/Tokyo")
LAST_EVALUABLE_SIGNAL_DATE = date(2026, 8, 27)
PROJECT_LAST_DATA_DATE = date(2026, 9, 30)
LEDGER = Path("out/event-forward-evidence/ledger.jsonl")
OUTCOME_LEDGER = Path("out/event-forward-evidence/outcomes.jsonl")
READINESS_REPORT = Path("out/event-forward-evidence/kill-switch-readiness.json")
OHLCV_ARCHIVE = Path("data/reference/daily_ohlcv_20210625_20260624_bydate.csv")
ARTIFACT_DIR = Path("out/event-paper-observation")


def parse_now(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("--now must include a UTC offset")
    return parsed


def previous_jst_date(now: datetime) -> date:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(TOKYO).date() - timedelta(days=1)


def artifact_paths(signal_date: date) -> tuple[Path, Path]:
    stem = f"causal-candidates-{signal_date.isoformat()}"
    return ARTIFACT_DIR / f"{stem}.json", ARTIFACT_DIR / f"{stem}.csv"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ledger_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid ledger JSON at line {line_number}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"invalid ledger row at line {line_number}")
        rows.append(row)
    return rows


def completion_state(signal_date: date) -> str:
    artifact_json, artifact_csv = artifact_paths(signal_date)
    matching_rows = [
        row for row in _ledger_rows(LEDGER) if row.get("signal_date") == signal_date.isoformat()
    ]
    paths_exist = (artifact_json.exists(), artifact_csv.exists())

    if not any(paths_exist) and not matching_rows:
        return "pending"
    if all(paths_exist) and len(matching_rows) == 1:
        row = matching_rows[0]
        if row.get("artifact_path") == str(artifact_json) and row.get("artifact_sha256") == _sha256(
            artifact_json
        ):
            return "completed"
    return "inconsistent"


def evidence_command(signal_date: date) -> list[str]:
    return [
        sys.executable,
        "scripts/run-event-forward-evidence.py",
        "--signal-date",
        signal_date.isoformat(),
    ]


def outcome_maintenance_commands(data_date: date) -> list[list[str]]:
    value = data_date.isoformat()
    return [
        [
            sys.executable,
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
            sys.executable,
            "scripts/finalize-event-forward-outcomes.py",
            "--ledger",
            str(LEDGER),
            "--outcomes",
            str(OUTCOME_LEDGER),
            "--ohlcv",
            str(OHLCV_ARCHIVE),
        ],
        [
            sys.executable,
            "scripts/report-project-kill-switch-readiness.py",
            "--ledger",
            str(LEDGER),
            "--outcomes",
            str(OUTCOME_LEDGER),
            "--output-json",
            str(READINESS_REPORT),
        ],
    ]


def run_daily(*, now: datetime, dry_run: bool = False) -> int:
    signal_date = previous_jst_date(now)
    checked_at = now.astimezone(TOKYO).isoformat()

    if signal_date > PROJECT_LAST_DATA_DATE:
        print(
            "shadow_forward_daily skip=after_project_window "
            f"signal_date={signal_date.isoformat()} checked_at={checked_at}"
        )
        return 0
    if not is_tse_business_day(signal_date):
        print(
            "shadow_forward_daily skip=non_business_day "
            f"signal_date={signal_date.isoformat()} checked_at={checked_at}"
        )
        return 0

    if signal_date > LAST_EVALUABLE_SIGNAL_DATE:
        commands = outcome_maintenance_commands(signal_date)
        if dry_run:
            print(
                "shadow_forward_daily action=would_maintain_outcomes "
                f"data_date={signal_date.isoformat()} commands={len(commands)}"
            )
            return 0
        print(
            "shadow_forward_daily action=maintain_outcomes "
            f"data_date={signal_date.isoformat()} checked_at={checked_at}",
            flush=True,
        )
        for maintenance_command in commands:
            subprocess.run(maintenance_command, check=True)
        print(
            f"shadow_forward_daily status=outcomes_maintained data_date={signal_date.isoformat()}"
        )
        return 0

    state = completion_state(signal_date)
    if state == "completed":
        print(
            "shadow_forward_daily skip=already_completed "
            f"signal_date={signal_date.isoformat()} checked_at={checked_at}"
        )
        return 0
    if state == "inconsistent":
        raise RuntimeError(
            "shadow-forward artifact/ledger state is inconsistent for "
            f"signal_date={signal_date.isoformat()}"
        )

    run_command = evidence_command(signal_date)
    if dry_run:
        print(
            "shadow_forward_daily action=would_run "
            f"signal_date={signal_date.isoformat()} command={' '.join(run_command)}"
        )
        return 0

    print(
        "shadow_forward_daily action=run "
        f"signal_date={signal_date.isoformat()} checked_at={checked_at}",
        flush=True,
    )
    subprocess.run(run_command, check=True)
    if completion_state(signal_date) != "completed":
        raise RuntimeError(
            "shadow-forward command returned success without a bound artifact/ledger row for "
            f"signal_date={signal_date.isoformat()}"
        )
    print(f"shadow_forward_daily status=completed signal_date={signal_date.isoformat()}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--now",
        type=parse_now,
        help="Override the current time for deterministic validation only.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve the target date and print the action without API access or writes.",
    )
    args = parser.parse_args()
    now = datetime.now(UTC) if args.now is None else args.now
    return run_daily(now=now, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
