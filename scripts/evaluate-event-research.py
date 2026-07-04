#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from datetime import date
from pathlib import Path

from event_research_common import (
    EVALUATION_SPLITS,
    RANDOM_BASELINE_NAMES,
    build_random_date_observations,
    evaluate_observations,
    read_jsonl,
    read_master_csv,
    read_ohlcv_csv,
    read_split_manifest,
    select_observations_for_split,
)
from trade_contracts.event_research import ObservationRecord


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate event research alpha arms.")
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--ohlcv", type=Path)
    parser.add_argument("--master", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("out/event-research"))
    parser.add_argument("--random-seeds", type=int, default=300)
    parser.add_argument(
        "--max-trading-days",
        type=int,
        help="Evaluate only the first N signal trading days for bounded real-data audits.",
    )
    parser.add_argument(
        "--start-date",
        help="First signal_date to include, in YYYY-MM-DD. Used before --max-trading-days.",
    )
    parser.add_argument(
        "--end-date",
        help="Last signal_date to include, in YYYY-MM-DD.",
    )
    parser.add_argument(
        "--event-type",
        action="append",
        help="Restrict observations to one or more event_type values.",
    )
    parser.add_argument(
        "--split",
        choices=EVALUATION_SPLITS,
        default="development",
        help="Evaluation split. Default excludes locked OOS details.",
    )
    parser.add_argument(
        "--include-locked-oos",
        action="store_true",
        help="Required when --split is locked-oos or all.",
    )
    parser.add_argument(
        "--split-manifest",
        type=Path,
        help="Freeze train/validation/locked OOS boundaries from an existing manifest JSON.",
    )
    args = parser.parse_args()

    if args.split in {"locked-oos", "all"} and not args.include_locked_oos:
        parser.error("--include-locked-oos is required when --split is locked-oos or all")
    all_observations = _read_observations(
        args.observations,
        max_trading_days=args.max_trading_days,
        start_date=None if args.start_date is None else date.fromisoformat(args.start_date),
        end_date=None if args.end_date is None else date.fromisoformat(args.end_date),
        event_types=None if args.event_type is None else set(args.event_type),
    )
    observations, split_info = select_observations_for_split(
        all_observations,
        split=args.split,
        fixed_split_manifest=read_split_manifest(args.split_manifest)
        if args.split_manifest
        else None,
    )
    random_date_observations = None
    if args.ohlcv is not None:
        random_date_observations = build_random_date_observations(
            ohlcv_rows=read_ohlcv_csv(args.ohlcv),
            master=read_master_csv(args.master),
            symbols={obs.symbol for obs in observations},
        )
    result = evaluate_observations(
        observations,
        random_seed_count=args.random_seeds,
        random_date_observations=random_date_observations,
    )
    result["evaluation_split"] = split_info
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "event-alpha-report.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "random-baselines.json").write_text(
        json.dumps(result["random_baselines"], indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    with (args.output_dir / "event-alpha-summary.csv").open("w", encoding="utf-8", newline="") as f:
        fieldnames = [
            "split",
            "event_type",
            "entry_arm",
            "exit_arm",
            "event_count",
            "trade_count",
            "net_pnl",
            "profit_factor",
            "max_drawdown",
            "average_return",
            "median_return",
            "hit_rate",
            "positive_month_ratio",
            "worst_month",
            *[f"{name}_percentile" for name in RANDOM_BASELINE_NAMES],
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in result["rows"]:
            writer.writerow(
                {key: args.split if key == "split" else row.get(key) for key in fieldnames}
            )
    print(
        "event_alpha "
        f"split={args.split} observations={len(observations)} "
        f"rows={len(result['rows'])} output={args.output_dir}"
    )
    return 0


def _read_observations(
    path: Path,
    *,
    max_trading_days: int | None,
    start_date: date | None,
    end_date: date | None,
    event_types: set[str] | None,
) -> list[ObservationRecord]:
    if max_trading_days is None and start_date is None and end_date is None and event_types is None:
        return [ObservationRecord.model_validate(row) for row in read_jsonl(path)]
    selected_dates = set(
        sorted(
            {
                date.fromisoformat(str(row["signal_date"]))
                for row in _iter_jsonl(path)
                if _date_in_range(
                    date.fromisoformat(str(row["signal_date"])),
                    start_date=start_date,
                    end_date=end_date,
                )
                and (event_types is None or row.get("event_type") in event_types)
            }
        )[:max_trading_days]
        if max_trading_days is not None
        else sorted(
            {
                date.fromisoformat(str(row["signal_date"]))
                for row in _iter_jsonl(path)
                if _date_in_range(
                    date.fromisoformat(str(row["signal_date"])),
                    start_date=start_date,
                    end_date=end_date,
                )
                and (event_types is None or row.get("event_type") in event_types)
            }
        )
    )
    return [
        ObservationRecord.model_validate(row)
        for row in _iter_jsonl(path)
        if date.fromisoformat(str(row["signal_date"])) in selected_dates
        and (event_types is None or row.get("event_type") in event_types)
    ]


def _date_in_range(value: date, *, start_date: date | None, end_date: date | None) -> bool:
    return (start_date is None or value >= start_date) and (end_date is None or value <= end_date)


def _iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


if __name__ == "__main__":
    raise SystemExit(main())
