from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trade_contracts.event_research import EventAiJob, EventRecord, ObservationRecord

from .prompt import PROMPT_VERSION, build_event_prompt, prompt_hash


def build_event_ai_job(
    *,
    event: EventRecord,
    observation: ObservationRecord,
    model_provider: str,
    model_id: str,
    temperature: Decimal = Decimal("0"),
    seed: int | None = None,
) -> EventAiJob:
    prompt = build_event_prompt(event, observation)
    digest = prompt_hash(prompt)
    return EventAiJob(
        job_id=f"{event.event_id}:{PROMPT_VERSION}:{digest[:12]}",
        event_id=event.event_id,
        prompt_version=PROMPT_VERSION,
        prompt_hash=digest,
        prompt=prompt,
        feature_schema_version="event_research_v0",
        feature_cutoff_at=observation.feature_cutoff_at,
        model_provider=model_provider,
        model_id=model_id,
        temperature=temperature,
        seed=seed,
        created_at=datetime.now(tz=UTC),
    )
