#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from event_research_common import RANDOM_BASELINE_NAMES, evaluate_observations, read_jsonl
from trade_contracts.event_research import ObservationRecord


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate event research alpha arms.")
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("out/event-research"))
    parser.add_argument("--random-seeds", type=int, default=300)
    args = parser.parse_args()

    observations = [ObservationRecord.model_validate(row) for row in read_jsonl(args.observations)]
    result = evaluate_observations(observations, random_seed_count=args.random_seeds)
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
            writer.writerow({key: row.get(key) for key in fieldnames})
    print(f"event_alpha rows={len(result['rows'])} output={args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
