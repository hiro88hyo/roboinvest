#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from event_research_common import (
    build_events_from_financial_rows,
    build_observations,
    read_jsonl,
    read_master_csv,
    read_ohlcv_csv,
    split_manifest,
    write_jsonl,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build point-in-time event research dataset.")
    parser.add_argument("--financial-summary-jsonl", type=Path, required=True)
    parser.add_argument("--ohlcv", type=Path, required=True)
    parser.add_argument("--master", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("out/event-research"))
    parser.add_argument("--fetched-at", default=None)
    args = parser.parse_args()

    fetched_at = (
        datetime.fromisoformat(args.fetched_at) if args.fetched_at else datetime.now(tz=UTC)
    )
    financial_rows = read_jsonl(args.financial_summary_jsonl)
    ohlcv = read_ohlcv_csv(args.ohlcv)
    master = read_master_csv(args.master)
    events = build_events_from_financial_rows(
        financial_rows,
        ohlcv_rows=ohlcv,
        fetched_at=fetched_at,
    )
    observations = build_observations(events, ohlcv_rows=ohlcv, master=master)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.output_dir / "events.jsonl", events)
    write_jsonl(args.output_dir / "observations.jsonl", observations)
    manifest = {
        "created_at": datetime.now(tz=UTC).isoformat(),
        "financial_summary_input": str(args.financial_summary_jsonl),
        "ohlcv_input": str(args.ohlcv),
        "master_input": None if args.master is None else str(args.master),
        "event_count": len(events),
        "observation_count": len(observations),
        "event_type_counts": _event_counts(events),
        **split_manifest(observations),
        "data_limitations": [
            "TOPIX and sector excess are reported only when source series are present.",
            "buyback_announcement is fixture/interface-only unless TDnet/archive rows "
            "are supplied.",
            "J-Quants /fins/summary fields are accepted from sanitized fixture or "
            "real API rows; unavailable fields stay null.",
        ],
    }
    (args.output_dir / "dataset-manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        "event_dataset "
        f"events={len(events)} observations={len(observations)} "
        f"output={args.output_dir}"
    )
    return 0


def _event_counts(events: list[object]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        event_type = event.event_type.value
        counts[event_type] = counts.get(event_type, 0) + 1
    return counts


if __name__ == "__main__":
    raise SystemExit(main())
