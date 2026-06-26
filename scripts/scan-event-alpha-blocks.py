#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from event_research_common import (
    EXIT_ARMS_FOR_REPORT,
    entry_arm_allows,
    metrics_for_observations,
)
from trade_contracts.event_research import EntryArm, ObservationRecord

ENTRY_ARMS_FOR_SCAN = (
    EntryArm.EVENT_ONLY,
    EntryArm.EVENT_PLUS_FUNDAMENTAL,
    EntryArm.EVENT_PLUS_TECHNICAL,
    EntryArm.EVENT_PLUS_FUNDAMENTAL_PLUS_TECHNICAL,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan event alpha stability by trading-day block.")
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("out/event-research-block-scan"))
    parser.add_argument("--block-trading-days", type=int, default=60)
    parser.add_argument("--step-trading-days", type=int)
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--min-trades", type=int, default=30)
    args = parser.parse_args()
    if args.block_trading_days < 1:
        parser.error("--block-trading-days must be >= 1")
    if args.step_trading_days is not None and args.step_trading_days < 1:
        parser.error("--step-trading-days must be >= 1")
    step_days = args.step_trading_days or args.block_trading_days
    start_date = None if args.start_date is None else date.fromisoformat(args.start_date)
    end_date = None if args.end_date is None else date.fromisoformat(args.end_date)

    trading_dates = _trading_dates(args.observations, start_date=start_date, end_date=end_date)
    blocks = _build_blocks(
        trading_dates,
        block_trading_days=args.block_trading_days,
        step_trading_days=step_days,
    )
    block_observations = _read_observations_by_block(args.observations, blocks)
    rows = _scan_blocks(blocks, block_observations)
    summary = _summarize(rows, min_trades=args.min_trades)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(args.output_dir / "event-alpha-blocks.csv", rows)
    _write_csv(args.output_dir / "event-alpha-block-summary.csv", summary)
    (args.output_dir / "event-alpha-block-summary.json").write_text(
        json.dumps(
            {
                "block_trading_days": args.block_trading_days,
                "step_trading_days": step_days,
                "block_count": len(blocks),
                "min_trades": args.min_trades,
                "summary": summary,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"event_alpha_block_scan blocks={len(blocks)} rows={len(rows)} output={args.output_dir}")
    return 0


def _trading_dates(path: Path, *, start_date: date | None, end_date: date | None) -> list[date]:
    dates = {
        date.fromisoformat(str(row["signal_date"]))
        for row in _iter_jsonl(path)
        if _date_in_range(date.fromisoformat(str(row["signal_date"])), start_date, end_date)
    }
    return sorted(dates)


def _build_blocks(
    trading_dates: list[date],
    *,
    block_trading_days: int,
    step_trading_days: int,
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for start_idx in range(0, len(trading_dates), step_trading_days):
        block_dates = trading_dates[start_idx : start_idx + block_trading_days]
        if not block_dates:
            continue
        blocks.append(
            {
                "block_id": f"block_{len(blocks):03d}",
                "start": block_dates[0],
                "end": block_dates[-1],
                "dates": set(block_dates),
            }
        )
    return blocks


def _read_observations_by_block(
    path: Path,
    blocks: list[dict[str, Any]],
) -> dict[str, list[ObservationRecord]]:
    date_to_block_ids: dict[date, list[str]] = defaultdict(list)
    for block in blocks:
        for item in block["dates"]:
            date_to_block_ids[item].append(block["block_id"])
    out: dict[str, list[ObservationRecord]] = {block["block_id"]: [] for block in blocks}
    for row in _iter_jsonl(path):
        signal_date = date.fromisoformat(str(row["signal_date"]))
        block_ids = date_to_block_ids.get(signal_date)
        if not block_ids:
            continue
        obs = ObservationRecord.model_validate(row)
        for block_id in block_ids:
            out[block_id].append(obs)
    return out


def _scan_blocks(
    blocks: list[dict[str, Any]],
    block_observations: dict[str, list[ObservationRecord]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for block in blocks:
        observations = block_observations[block["block_id"]]
        for event_type in sorted(
            {obs.event_type for obs in observations},
            key=lambda item: item.value,
        ):
            event_observations = [obs for obs in observations if obs.event_type == event_type]
            for entry_arm in ENTRY_ARMS_FOR_SCAN:
                selected = [obs for obs in event_observations if entry_arm_allows(obs, entry_arm)]
                for exit_arm in EXIT_ARMS_FOR_REPORT:
                    metrics = metrics_for_observations(
                        selected,
                        exit_arm=exit_arm,
                        include_bootstrap_ci=False,
                    )
                    rows.append(
                        {
                            "block_id": block["block_id"],
                            "block_start": block["start"].isoformat(),
                            "block_end": block["end"].isoformat(),
                            "event_type": event_type.value,
                            "entry_arm": entry_arm.value,
                            "exit_arm": exit_arm.value,
                            **{
                                key: metrics[key]
                                for key in (
                                    "event_count",
                                    "duplicate_trade_count",
                                    "trade_count",
                                    "net_pnl",
                                    "profit_factor",
                                    "max_drawdown",
                                    "average_return",
                                    "median_return",
                                    "hit_rate",
                                    "positive_month_ratio",
                                    "worst_month",
                                )
                            },
                        }
                    )
    return rows


def _summarize(rows: list[dict[str, Any]], *, min_trades: int) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_key[(row["event_type"], row["entry_arm"], row["exit_arm"])].append(row)
    summary: list[dict[str, Any]] = []
    for (event_type, entry_arm, exit_arm), group in sorted(by_key.items()):
        valid = [row for row in group if int(row["trade_count"]) >= min_trades]
        pnls = [float(row["net_pnl"]) for row in valid]
        pfs = [
            float(row["profit_factor"]) for row in valid if row["profit_factor"] not in (None, "")
        ]
        summary.append(
            {
                "event_type": event_type,
                "entry_arm": entry_arm,
                "exit_arm": exit_arm,
                "block_count": len(group),
                "valid_block_count": len(valid),
                "positive_block_ratio": None
                if not pnls
                else sum(1 for pnl in pnls if pnl > 0) / len(pnls),
                "total_net_pnl": sum(pnls),
                "worst_block_pnl": None if not pnls else min(pnls),
                "median_block_pnl": None if not pnls else _median(pnls),
                "median_profit_factor": None if not pfs else _median(pfs),
                "total_trades": sum(int(row["trade_count"]) for row in valid),
                "min_trades": min_trades,
            }
        )
    return summary


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def _date_in_range(value: date, start_date: date | None, end_date: date | None) -> bool:
    return (start_date is None or value >= start_date) and (end_date is None or value <= end_date)


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


if __name__ == "__main__":
    raise SystemExit(main())
