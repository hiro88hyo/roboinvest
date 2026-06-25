#!/usr/bin/env python3
from __future__ import annotations

import argparse
from decimal import Decimal
from pathlib import Path

from event_research_common import read_jsonl, write_jsonl
from strategy_ai.event.jobs import build_event_ai_job
from trade_contracts.event_research import EventRecord, ObservationRecord


def main() -> int:
    parser = argparse.ArgumentParser(description="Build deterministic event LLM jobs.")
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("out/event-ai/jobs.jsonl"))
    parser.add_argument("--model-provider", default="fixture")
    parser.add_argument("--model-id", default="fixture-event-labeler-v0")
    parser.add_argument("--temperature", default="0")
    parser.add_argument("--seed", type=int)
    args = parser.parse_args()

    events = {row["event_id"]: EventRecord.model_validate(row) for row in read_jsonl(args.events)}
    observations = [ObservationRecord.model_validate(row) for row in read_jsonl(args.observations)]
    jobs = [
        build_event_ai_job(
            event=events[obs.event_id],
            observation=obs,
            model_provider=args.model_provider,
            model_id=args.model_id,
            temperature=Decimal(args.temperature),
            seed=args.seed,
        )
        for obs in observations
        if obs.event_id in events
    ]
    write_jsonl(args.output, jobs)
    print(f"event_llm_jobs count={len(jobs)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
