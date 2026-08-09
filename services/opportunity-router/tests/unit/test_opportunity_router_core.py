from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from opportunity_router._testing import (
    DECISION_AT,
    make_candidate,
    make_intake,
    make_playbook,
    make_policy,
    make_population,
)
from opportunity_router.core import evaluate_candidate, route_batch
from opportunity_router.models import (
    CapacityRule,
    CounterfactualClass,
    DecisionKind,
    GateResults,
    GateState,
    PlaybookRegistration,
    PopulationSnapshot,
    ReasonCode,
    RouterPolicy,
)


def test_all_gates_pass_returns_enter_shadow_fixture_only() -> None:
    population = make_population()
    decision = evaluate_candidate(make_policy(), population, make_candidate(population))

    assert decision.decision is DecisionKind.ENTER_SHADOW
    assert decision.reason_codes == (ReasonCode.ALL_GATES_PASS,)
    assert decision.playbook_id == "event_revision_v1"
    assert decision.counterfactual_class is CounterfactualClass.POLICY_EVALUABLE
    assert decision.policy_sha256 == make_policy().declared_sha256
    assert decision.candidate_intake_contract_sha256 == make_intake().declared_sha256
    assert decision.playbook_contract_sha256 == make_playbook().declared_sha256
    assert decision.assignment_rule_version == make_policy().version


def test_no_admitted_playbooks_disables_policy() -> None:
    population = make_population()
    decision = evaluate_candidate(
        make_policy(playbooks=()),
        population,
        make_candidate(population),
    )

    assert decision.decision is DecisionKind.POLICY_DISABLED
    assert decision.reason_codes == (ReasonCode.NO_ADMITTED_PLAYBOOKS,)


def test_policy_hash_mismatch_fails_closed() -> None:
    population = make_population()
    policy = replace(make_policy(), declared_sha256="0" * 64)

    decision = evaluate_candidate(policy, population, make_candidate(population))

    assert decision.decision is DecisionKind.POLICY_DISABLED
    assert decision.reason_codes == (ReasonCode.POLICY_HASH_MISMATCH,)


def test_playbook_hash_mismatch_fails_closed() -> None:
    population = make_population()
    valid = make_playbook()
    invalid = replace(valid, declared_sha256="f" * 64)
    policy = make_policy(playbooks=(invalid,))

    decision = evaluate_candidate(policy, population, make_candidate(population))

    assert decision.decision is DecisionKind.POLICY_DISABLED
    assert decision.reason_codes == (ReasonCode.PLAYBOOK_HASH_MISMATCH,)


def test_candidate_intake_contract_hash_mismatch_fails_closed() -> None:
    population = make_population()
    invalid_intake = replace(make_intake(), declared_sha256="e" * 64)
    policy = make_policy()
    policy = RouterPolicy.build(
        policy_id=policy.policy_id,
        version=policy.version,
        effective_at=policy.effective_at,
        expires_at=policy.expires_at,
        candidate_intake=invalid_intake,
        playbooks=policy.playbooks,
        max_entries=policy.max_entries,
        capacity_rule=policy.capacity_rule,
        enabled=policy.enabled,
    )

    decision = evaluate_candidate(policy, population, make_candidate(population))

    assert decision.decision is DecisionKind.POLICY_DISABLED
    assert decision.reason_codes == (ReasonCode.INTAKE_CONTRACT_HASH_MISMATCH,)


def test_population_hash_mismatch_fails_closed() -> None:
    population = make_population()
    candidate = make_candidate(population, population_hash="1" * 64)

    decision = evaluate_candidate(make_policy(), population, candidate)

    assert decision.decision is DecisionKind.NO_TRADE
    assert decision.reason_codes == (ReasonCode.POPULATION_HASH_MISMATCH,)
    assert decision.counterfactual_class is CounterfactualClass.ADMINISTRATIVE_TERMINAL


def test_intake_version_mismatch_fails_closed() -> None:
    population = make_population()
    candidate = make_candidate(population, intake_version="intake-v2")

    decision = evaluate_candidate(make_policy(), population, candidate)

    assert decision.decision is DecisionKind.NO_TRADE
    assert decision.reason_codes == (ReasonCode.INTAKE_VERSION_MISMATCH,)


def test_population_must_bind_the_policy_intake_contract_hash() -> None:
    population = make_population()
    mismatched = PopulationSnapshot.build(
        intake_version=population.intake_version,
        intake_contract_sha256="a" * 64,
        session_id=population.session_id,
        eligible_instruments=population.eligible_instruments,
        candidate_ids=population.candidate_ids,
    )
    candidate = make_candidate(mismatched)

    decision = evaluate_candidate(make_policy(), mismatched, candidate)

    assert decision.decision is DecisionKind.NO_TRADE
    assert decision.reason_codes == (ReasonCode.INTAKE_CONTRACT_HASH_MISMATCH,)


def test_candidate_not_in_bound_population_fails_closed() -> None:
    population = make_population()
    candidate = make_candidate(population, candidate_id="not-registered")

    decision = evaluate_candidate(make_policy(), population, candidate)

    assert decision.decision is DecisionKind.NO_TRADE
    assert decision.reason_codes == (ReasonCode.CANDIDATE_NOT_IN_POPULATION,)


def test_ambiguous_playbook_is_no_trade() -> None:
    population = make_population()
    second = make_playbook("event_buyback_v1")
    policy = make_policy(playbooks=(make_playbook(), second))
    candidate = make_candidate(
        population,
        matched_playbook_ids=("event_revision_v1", "event_buyback_v1"),
    )

    decision = evaluate_candidate(policy, population, candidate)

    assert decision.decision is DecisionKind.NO_TRADE
    assert decision.reason_codes == (ReasonCode.AMBIGUOUS_PLAYBOOK,)


@pytest.mark.parametrize(
    ("matched_playbooks", "playbooks", "expected_reason"),
    [
        ((), None, ReasonCode.NO_PLAYBOOK_MATCH),
        (("not-admitted",), None, ReasonCode.PLAYBOOK_NOT_ADMITTED),
        (
            ("event_revision_v1",),
            (make_playbook(enabled=False),),
            ReasonCode.PLAYBOOK_DISABLED,
        ),
    ],
)
def test_playbook_assignment_failures_are_no_trade(
    matched_playbooks: tuple[str, ...],
    playbooks: tuple[PlaybookRegistration, ...] | None,
    expected_reason: ReasonCode,
) -> None:
    population = make_population()
    candidate = make_candidate(population, matched_playbook_ids=matched_playbooks)

    decision = evaluate_candidate(make_policy(playbooks=playbooks), population, candidate)

    assert decision.decision is DecisionKind.NO_TRADE
    assert decision.reason_codes == (expected_reason,)


def test_failed_context_gate_is_policy_evaluable_no_trade() -> None:
    population = make_population()
    candidate = make_candidate(
        population,
        gates=GateResults(
            evidence=GateState.PASS,
            mechanism=GateState.PASS,
            context=GateState.FAIL,
            execution=GateState.PASS,
            portfolio_precheck=GateState.PASS,
        ),
    )

    decision = evaluate_candidate(make_policy(), population, candidate)

    assert decision.decision is DecisionKind.NO_TRADE
    assert decision.reason_codes == (ReasonCode.CONTEXT_FAIL,)
    assert decision.counterfactual_class is CounterfactualClass.POLICY_EVALUABLE


def test_unknown_or_execution_gate_is_not_executable_counterfactual() -> None:
    population = make_population()
    candidate = make_candidate(
        population,
        gates=GateResults(
            evidence=GateState.UNKNOWN,
            mechanism=GateState.PASS,
            context=GateState.PASS,
            execution=GateState.FAIL,
            portfolio_precheck=GateState.PASS,
        ),
    )

    decision = evaluate_candidate(make_policy(), population, candidate)

    assert decision.reason_codes == (
        ReasonCode.EVIDENCE_UNKNOWN,
        ReasonCode.EXECUTION_FAIL,
    )
    assert decision.counterfactual_class is CounterfactualClass.ECONOMIC_ONLY_NOT_EXECUTABLE


def test_execution_failure_without_unknown_is_not_executable_counterfactual() -> None:
    population = make_population()
    candidate = make_candidate(
        population,
        gates=GateResults(
            evidence=GateState.PASS,
            mechanism=GateState.PASS,
            context=GateState.PASS,
            execution=GateState.FAIL,
            portfolio_precheck=GateState.PASS,
        ),
    )

    decision = evaluate_candidate(make_policy(), population, candidate)

    assert decision.reason_codes == (ReasonCode.EXECUTION_FAIL,)
    assert decision.counterfactual_class is CounterfactualClass.ECONOMIC_ONLY_NOT_EXECUTABLE


def test_expired_and_duplicate_are_distinct_terminal_decisions() -> None:
    population = make_population(candidate_ids=("expired", "duplicate"))
    expired = make_candidate(
        population,
        candidate_id="expired",
        valid_until=DECISION_AT - timedelta(seconds=1),
    )
    duplicate = make_candidate(
        population,
        candidate_id="duplicate",
        duplicate_of_candidate_id="original",
    )

    expired_decision = evaluate_candidate(make_policy(), population, expired)
    duplicate_decision = evaluate_candidate(make_policy(), population, duplicate)

    assert expired_decision.decision is DecisionKind.EXPIRED
    assert duplicate_decision.decision is DecisionKind.DUPLICATE


def test_future_evidence_cutoff_is_rejected() -> None:
    population = make_population()
    candidate = make_candidate(
        population,
        evidence_cutoff_at=DECISION_AT + timedelta(seconds=1),
    )

    decision = evaluate_candidate(make_policy(), population, candidate)

    assert decision.decision is DecisionKind.NO_TRADE
    assert decision.reason_codes == (ReasonCode.EVIDENCE_CUTOFF_AFTER_DECISION,)


@pytest.mark.parametrize(
    ("policy", "decision_at", "expected_reason"),
    [
        (make_policy(enabled=False), DECISION_AT, ReasonCode.POLICY_DISABLED),
        (
            make_policy(),
            make_policy().effective_at - timedelta(seconds=1),
            ReasonCode.POLICY_NOT_EFFECTIVE,
        ),
        (make_policy(), make_policy().expires_at, ReasonCode.POLICY_EXPIRED),
    ],
)
def test_policy_lifecycle_fails_closed(
    policy: RouterPolicy,
    decision_at: datetime,
    expected_reason: ReasonCode,
) -> None:
    population = make_population()
    candidate = make_candidate(population, decision_at=decision_at)

    decision = evaluate_candidate(policy, population, candidate)

    assert decision.decision is DecisionKind.POLICY_DISABLED
    assert decision.reason_codes == (expected_reason,)


def test_decision_identity_is_stable_for_same_registered_identity() -> None:
    population = make_population()
    policy = make_policy()
    candidate = make_candidate(population)

    first = evaluate_candidate(policy, population, candidate)
    second = evaluate_candidate(policy, population, candidate)

    assert first == second
    assert first.decision_id.startswith("router-")


def test_capacity_without_preregistered_rule_rejects_all_eligible() -> None:
    population = make_population(
        candidate_ids=("candidate-1", "candidate-2"),
        instruments=("7203", "6758"),
    )
    candidates = (
        make_candidate(population, candidate_id="candidate-1", instrument="7203"),
        make_candidate(population, candidate_id="candidate-2", instrument="6758"),
    )

    decisions = route_batch(make_policy(max_entries=1), population, candidates)

    assert {decision.decision for decision in decisions} == {DecisionKind.NO_TRADE}
    assert {decision.reason_codes for decision in decisions} == {
        (ReasonCode.CAPACITY_RULE_MISSING,)
    }


def test_batch_below_capacity_preserves_eligible_decisions() -> None:
    population = make_population()

    decisions = route_batch(make_policy(max_entries=2), population, (make_candidate(population),))

    assert len(decisions) == 1
    assert decisions[0].decision is DecisionKind.ENTER_SHADOW


def test_capacity_resolution_is_deterministic_and_input_order_independent() -> None:
    population = make_population(
        candidate_ids=("candidate-a", "candidate-b", "candidate-c"),
        instruments=("7203", "6758", "9984"),
    )
    candidates = (
        make_candidate(
            population,
            candidate_id="candidate-a",
            instrument="7203",
            sector="auto",
            priority=2,
        ),
        make_candidate(
            population,
            candidate_id="candidate-b",
            instrument="6758",
            sector="electronics",
            priority=1,
        ),
        make_candidate(
            population,
            candidate_id="candidate-c",
            instrument="9984",
            sector="electronics",
            priority=1,
        ),
    )
    rule = CapacityRule(
        rule_id="capacity-v1",
        version="1.0.0",
        playbook_priority=("event_revision_v1",),
        same_sector_limit=1,
    )
    policy = make_policy(max_entries=2, capacity_rule=rule)

    forward = route_batch(policy, population, candidates)
    reversed_input = route_batch(policy, population, reversed(candidates))

    assert forward == reversed_input
    entered = [
        decision.candidate_id
        for decision in forward
        if decision.decision is DecisionKind.ENTER_SHADOW
    ]
    rejected = [decision for decision in forward if decision.decision is DecisionKind.NO_TRADE]
    assert set(entered) == {"candidate-a", "candidate-b"}
    assert len(rejected) == 1
    assert rejected[0].candidate_id == "candidate-c"
    assert rejected[0].reason_codes == (ReasonCode.CAPACITY_REJECTED,)


def test_capacity_rule_requires_all_playbooks_in_priority() -> None:
    population = make_population(
        candidate_ids=("candidate-1", "candidate-2"),
        instruments=("7203", "6758"),
    )
    second = PlaybookRegistration.build(
        playbook_id="event_buyback_v1",
        version="1.0.0",
        contract={"mechanism": "buyback"},
    )
    candidates = (
        make_candidate(population, candidate_id="candidate-1", instrument="7203"),
        make_candidate(
            population,
            candidate_id="candidate-2",
            instrument="6758",
            matched_playbook_ids=("event_buyback_v1",),
        ),
    )
    rule = CapacityRule(
        rule_id="capacity-v1",
        version="1.0.0",
        playbook_priority=("event_revision_v1",),
    )
    policy = make_policy(
        playbooks=(make_playbook(), second),
        max_entries=1,
        capacity_rule=rule,
    )

    decisions = route_batch(policy, population, candidates)

    assert {decision.reason_codes for decision in decisions} == {
        (ReasonCode.CAPACITY_PRIORITY_MISSING,)
    }


def test_capacity_enforces_same_instrument_limit_and_max_entries() -> None:
    population = make_population(
        candidate_ids=("candidate-a", "candidate-b", "candidate-c", "candidate-d"),
        instruments=("7203", "6758", "9984"),
    )
    candidates = (
        make_candidate(
            population,
            candidate_id="candidate-a",
            instrument="7203",
            sector=None,
            priority=1,
        ),
        make_candidate(
            population,
            candidate_id="candidate-b",
            instrument="7203",
            sector=None,
            priority=2,
        ),
        make_candidate(
            population,
            candidate_id="candidate-c",
            instrument="6758",
            sector=None,
            priority=3,
        ),
        make_candidate(
            population,
            candidate_id="candidate-d",
            instrument="9984",
            sector=None,
            priority=4,
        ),
    )
    rule = CapacityRule(
        rule_id="capacity-v1",
        version="1.0.0",
        playbook_priority=("event_revision_v1",),
    )

    decisions = route_batch(
        make_policy(max_entries=2, capacity_rule=rule),
        population,
        candidates,
    )

    entered = {
        decision.candidate_id
        for decision in decisions
        if decision.decision is DecisionKind.ENTER_SHADOW
    }
    assert entered == {"candidate-a", "candidate-c"}
    assert {
        decision.candidate_id
        for decision in decisions
        if decision.reason_codes == (ReasonCode.CAPACITY_REJECTED,)
    } == {"candidate-b", "candidate-d"}


def test_models_reject_naive_datetimes_and_more_than_three_playbooks() -> None:
    population = make_population()
    with pytest.raises(ValueError, match="timezone-aware"):
        make_candidate(population, decision_at=DECISION_AT.replace(tzinfo=None))

    playbooks = tuple(make_playbook(f"playbook-{index}") for index in range(4))
    with pytest.raises(ValueError, match="at most 3"):
        make_policy(playbooks=playbooks)

    assert make_candidate(population).gates.all_pass()
