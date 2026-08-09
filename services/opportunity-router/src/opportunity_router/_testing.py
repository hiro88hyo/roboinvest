"""Reusable test fixtures for opportunity-router tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from opportunity_router.models import (
    CandidateEvaluation,
    CandidateIntakeRegistration,
    CapacityRule,
    GateResults,
    GateState,
    PlaybookRegistration,
    PopulationSnapshot,
    RouterPolicy,
)

DECISION_AT = datetime(2026, 8, 10, 0, 15, tzinfo=UTC)


def make_intake() -> CandidateIntakeRegistration:
    return CandidateIntakeRegistration.build(
        version="intake-v1",
        contract={
            "source_id": "fixture-source",
            "detection_rule": "fixture-only",
            "evidence_cutoff_rule": "decision_at_minus_one_minute",
        },
    )


def make_playbook(
    playbook_id: str = "event_revision_v1",
    *,
    enabled: bool = True,
) -> PlaybookRegistration:
    return PlaybookRegistration.build(
        playbook_id=playbook_id,
        version="1.0.0",
        contract={"mechanism": playbook_id, "holding_days": 5},
        enabled=enabled,
    )


def make_population(
    candidate_ids: tuple[str, ...] = ("candidate-1",),
    instruments: tuple[str, ...] = ("7203",),
) -> PopulationSnapshot:
    intake = make_intake()
    return PopulationSnapshot.build(
        intake_version=intake.version,
        intake_contract_sha256=intake.declared_sha256,
        session_id="2026-08-10-preopen",
        eligible_instruments=instruments,
        candidate_ids=candidate_ids,
    )


def make_policy(
    *,
    playbooks: tuple[PlaybookRegistration, ...] | None = None,
    max_entries: int = 1,
    capacity_rule: CapacityRule | None = None,
    enabled: bool = True,
) -> RouterPolicy:
    registered = (make_playbook(),) if playbooks is None else playbooks
    return RouterPolicy.build(
        policy_id="router-policy-v1",
        version="1.0.0",
        effective_at=DECISION_AT - timedelta(days=1),
        expires_at=DECISION_AT + timedelta(days=30),
        candidate_intake=make_intake(),
        playbooks=registered,
        max_entries=max_entries,
        capacity_rule=capacity_rule,
        enabled=enabled,
    )


def make_candidate(
    population: PopulationSnapshot,
    *,
    candidate_id: str = "candidate-1",
    instrument: str = "7203",
    sector: str | None = "transportation_equipment",
    matched_playbook_ids: tuple[str, ...] = ("event_revision_v1",),
    gates: GateResults | None = None,
    priority: int = 0,
    decision_at: datetime = DECISION_AT,
    evidence_cutoff_at: datetime | None = None,
    valid_until: datetime | None = None,
    intake_version: str = "intake-v1",
    population_hash: str | None = None,
    duplicate_of_candidate_id: str | None = None,
) -> CandidateEvaluation:
    return CandidateEvaluation(
        candidate_id=candidate_id,
        instrument=instrument,
        sector=sector,
        decision_at=decision_at,
        evidence_cutoff_at=evidence_cutoff_at or decision_at - timedelta(minutes=1),
        valid_until=valid_until or decision_at + timedelta(minutes=15),
        candidate_intake_version=intake_version,
        upstream_population_hash=population_hash or population.declared_sha256,
        matched_playbook_ids=matched_playbook_ids,
        gates=gates
        or GateResults(
            evidence=GateState.PASS,
            mechanism=GateState.PASS,
            context=GateState.PASS,
            execution=GateState.PASS,
            portfolio_precheck=GateState.PASS,
        ),
        candidate_priority=priority,
        duplicate_of_candidate_id=duplicate_of_candidate_id,
    )
