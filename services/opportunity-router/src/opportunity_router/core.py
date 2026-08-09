"""Pure fail-closed routing for policy-authorized candidate evaluations."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import replace

from opportunity_router.integrity import canonical_sha256
from opportunity_router.models import (
    CandidateEvaluation,
    CounterfactualClass,
    DecisionKind,
    GateState,
    PlaybookRegistration,
    PopulationSnapshot,
    ReasonCode,
    RouterDecision,
    RouterPolicy,
)


def _decision_id(
    policy: RouterPolicy,
    candidate: CandidateEvaluation,
    playbook: PlaybookRegistration | None,
) -> str:
    identity = {
        "policy_id": policy.policy_id,
        "policy_version": policy.version,
        "candidate_id": candidate.candidate_id,
        "playbook_id": playbook.playbook_id if playbook is not None else None,
        "playbook_version": playbook.version if playbook is not None else None,
        "evidence_cutoff_at": candidate.evidence_cutoff_at.isoformat(),
    }
    return f"router-{canonical_sha256(identity)[:32]}"


def _decision(
    *,
    policy: RouterPolicy,
    candidate: CandidateEvaluation,
    playbook: PlaybookRegistration | None,
    decision: DecisionKind,
    reasons: tuple[ReasonCode, ...],
    counterfactual_class: CounterfactualClass,
) -> RouterDecision:
    return RouterDecision(
        decision_id=_decision_id(policy, candidate, playbook),
        decision_at=candidate.decision_at,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        policy_sha256=policy.declared_sha256,
        playbook_id=playbook.playbook_id if playbook is not None else None,
        playbook_version=playbook.version if playbook is not None else None,
        playbook_contract_sha256=playbook.declared_sha256 if playbook is not None else None,
        candidate_id=candidate.candidate_id,
        candidate_intake_version=candidate.candidate_intake_version,
        candidate_intake_contract_sha256=policy.candidate_intake.declared_sha256,
        upstream_population_hash=candidate.upstream_population_hash,
        instrument=candidate.instrument,
        sector=candidate.sector,
        evidence_cutoff_at=candidate.evidence_cutoff_at,
        valid_until=candidate.valid_until,
        matched_playbook_ids=candidate.matched_playbook_ids,
        assignment_rule_version=policy.version,
        capacity_rule_id=policy.capacity_rule.rule_id if policy.capacity_rule is not None else None,
        capacity_rule_version=(
            policy.capacity_rule.version if policy.capacity_rule is not None else None
        ),
        gates=candidate.gates,
        decision=decision,
        reason_codes=reasons,
        counterfactual_class=counterfactual_class,
        candidate_priority=candidate.candidate_priority,
    )


def _gate_reasons(candidate: CandidateEvaluation) -> tuple[ReasonCode, ...]:
    mapping = {
        "evidence": {
            GateState.FAIL: ReasonCode.EVIDENCE_FAIL,
            GateState.UNKNOWN: ReasonCode.EVIDENCE_UNKNOWN,
        },
        "mechanism": {
            GateState.FAIL: ReasonCode.MECHANISM_FAIL,
            GateState.UNKNOWN: ReasonCode.MECHANISM_UNKNOWN,
        },
        "context": {
            GateState.FAIL: ReasonCode.CONTEXT_FAIL,
            GateState.UNKNOWN: ReasonCode.CONTEXT_UNKNOWN,
        },
        "execution": {
            GateState.FAIL: ReasonCode.EXECUTION_FAIL,
            GateState.UNKNOWN: ReasonCode.EXECUTION_UNKNOWN,
        },
        "portfolio_precheck": {
            GateState.FAIL: ReasonCode.PORTFOLIO_FAIL,
            GateState.UNKNOWN: ReasonCode.PORTFOLIO_UNKNOWN,
        },
    }
    reasons: list[ReasonCode] = []
    for gate, state in candidate.gates.as_dict().items():
        reason = mapping[gate].get(state)
        if reason is not None:
            reasons.append(reason)
    return tuple(reasons)


def _gate_counterfactual_class(candidate: CandidateEvaluation) -> CounterfactualClass:
    states = candidate.gates.as_dict()
    if any(state is GateState.UNKNOWN for state in states.values()):
        return CounterfactualClass.ECONOMIC_ONLY_NOT_EXECUTABLE
    if (
        states["execution"] is not GateState.PASS
        or states["portfolio_precheck"] is not GateState.PASS
    ):
        return CounterfactualClass.ECONOMIC_ONLY_NOT_EXECUTABLE
    return CounterfactualClass.POLICY_EVALUABLE


def evaluate_candidate(
    policy: RouterPolicy,
    population: PopulationSnapshot,
    candidate: CandidateEvaluation,
) -> RouterDecision:
    """Evaluate one candidate without external I/O or mutable state."""

    if not policy.verify_integrity():
        return _decision(
            policy=policy,
            candidate=candidate,
            playbook=None,
            decision=DecisionKind.POLICY_DISABLED,
            reasons=(ReasonCode.POLICY_HASH_MISMATCH,),
            counterfactual_class=CounterfactualClass.ADMINISTRATIVE_TERMINAL,
        )
    if not policy.candidate_intake.verify_integrity():
        return _decision(
            policy=policy,
            candidate=candidate,
            playbook=None,
            decision=DecisionKind.POLICY_DISABLED,
            reasons=(ReasonCode.INTAKE_CONTRACT_HASH_MISMATCH,),
            counterfactual_class=CounterfactualClass.ADMINISTRATIVE_TERMINAL,
        )
    if any(not playbook.verify_integrity() for playbook in policy.playbooks):
        return _decision(
            policy=policy,
            candidate=candidate,
            playbook=None,
            decision=DecisionKind.POLICY_DISABLED,
            reasons=(ReasonCode.PLAYBOOK_HASH_MISMATCH,),
            counterfactual_class=CounterfactualClass.ADMINISTRATIVE_TERMINAL,
        )
    if not policy.enabled:
        return _decision(
            policy=policy,
            candidate=candidate,
            playbook=None,
            decision=DecisionKind.POLICY_DISABLED,
            reasons=(ReasonCode.POLICY_DISABLED,),
            counterfactual_class=CounterfactualClass.ADMINISTRATIVE_TERMINAL,
        )
    if candidate.decision_at < policy.effective_at:
        return _decision(
            policy=policy,
            candidate=candidate,
            playbook=None,
            decision=DecisionKind.POLICY_DISABLED,
            reasons=(ReasonCode.POLICY_NOT_EFFECTIVE,),
            counterfactual_class=CounterfactualClass.ADMINISTRATIVE_TERMINAL,
        )
    if candidate.decision_at >= policy.expires_at:
        return _decision(
            policy=policy,
            candidate=candidate,
            playbook=None,
            decision=DecisionKind.POLICY_DISABLED,
            reasons=(ReasonCode.POLICY_EXPIRED,),
            counterfactual_class=CounterfactualClass.ADMINISTRATIVE_TERMINAL,
        )
    if not policy.playbooks:
        return _decision(
            policy=policy,
            candidate=candidate,
            playbook=None,
            decision=DecisionKind.POLICY_DISABLED,
            reasons=(ReasonCode.NO_ADMITTED_PLAYBOOKS,),
            counterfactual_class=CounterfactualClass.ADMINISTRATIVE_TERMINAL,
        )
    if (
        candidate.candidate_intake_version != policy.candidate_intake_version
        or population.intake_version != policy.candidate_intake_version
    ):
        return _decision(
            policy=policy,
            candidate=candidate,
            playbook=None,
            decision=DecisionKind.NO_TRADE,
            reasons=(ReasonCode.INTAKE_VERSION_MISMATCH,),
            counterfactual_class=CounterfactualClass.ADMINISTRATIVE_TERMINAL,
        )
    if population.intake_contract_sha256 != policy.candidate_intake.declared_sha256:
        return _decision(
            policy=policy,
            candidate=candidate,
            playbook=None,
            decision=DecisionKind.NO_TRADE,
            reasons=(ReasonCode.INTAKE_CONTRACT_HASH_MISMATCH,),
            counterfactual_class=CounterfactualClass.ADMINISTRATIVE_TERMINAL,
        )
    if (
        not population.verify_integrity()
        or candidate.upstream_population_hash != population.declared_sha256
    ):
        return _decision(
            policy=policy,
            candidate=candidate,
            playbook=None,
            decision=DecisionKind.NO_TRADE,
            reasons=(ReasonCode.POPULATION_HASH_MISMATCH,),
            counterfactual_class=CounterfactualClass.ADMINISTRATIVE_TERMINAL,
        )
    if (
        candidate.candidate_id not in population.candidate_ids
        or candidate.instrument not in population.eligible_instruments
    ):
        return _decision(
            policy=policy,
            candidate=candidate,
            playbook=None,
            decision=DecisionKind.NO_TRADE,
            reasons=(ReasonCode.CANDIDATE_NOT_IN_POPULATION,),
            counterfactual_class=CounterfactualClass.ADMINISTRATIVE_TERMINAL,
        )
    if candidate.decision_at > candidate.valid_until:
        return _decision(
            policy=policy,
            candidate=candidate,
            playbook=None,
            decision=DecisionKind.EXPIRED,
            reasons=(ReasonCode.CANDIDATE_EXPIRED,),
            counterfactual_class=CounterfactualClass.ADMINISTRATIVE_TERMINAL,
        )
    if candidate.evidence_cutoff_at > candidate.decision_at:
        return _decision(
            policy=policy,
            candidate=candidate,
            playbook=None,
            decision=DecisionKind.NO_TRADE,
            reasons=(ReasonCode.EVIDENCE_CUTOFF_AFTER_DECISION,),
            counterfactual_class=CounterfactualClass.ADMINISTRATIVE_TERMINAL,
        )
    if candidate.duplicate_of_candidate_id is not None:
        return _decision(
            policy=policy,
            candidate=candidate,
            playbook=None,
            decision=DecisionKind.DUPLICATE,
            reasons=(ReasonCode.DUPLICATE_CANDIDATE,),
            counterfactual_class=CounterfactualClass.ADMINISTRATIVE_TERMINAL,
        )
    if not candidate.matched_playbook_ids:
        return _decision(
            policy=policy,
            candidate=candidate,
            playbook=None,
            decision=DecisionKind.NO_TRADE,
            reasons=(ReasonCode.NO_PLAYBOOK_MATCH,),
            counterfactual_class=CounterfactualClass.POLICY_EVALUABLE,
        )
    if len(candidate.matched_playbook_ids) > 1:
        return _decision(
            policy=policy,
            candidate=candidate,
            playbook=None,
            decision=DecisionKind.NO_TRADE,
            reasons=(ReasonCode.AMBIGUOUS_PLAYBOOK,),
            counterfactual_class=CounterfactualClass.POLICY_EVALUABLE,
        )

    playbook = policy.playbook_by_id(candidate.matched_playbook_ids[0])
    if playbook is None:
        return _decision(
            policy=policy,
            candidate=candidate,
            playbook=None,
            decision=DecisionKind.NO_TRADE,
            reasons=(ReasonCode.PLAYBOOK_NOT_ADMITTED,),
            counterfactual_class=CounterfactualClass.POLICY_EVALUABLE,
        )
    if not playbook.enabled:
        return _decision(
            policy=policy,
            candidate=candidate,
            playbook=playbook,
            decision=DecisionKind.NO_TRADE,
            reasons=(ReasonCode.PLAYBOOK_DISABLED,),
            counterfactual_class=CounterfactualClass.ADMINISTRATIVE_TERMINAL,
        )

    gate_reasons = _gate_reasons(candidate)
    if gate_reasons:
        return _decision(
            policy=policy,
            candidate=candidate,
            playbook=playbook,
            decision=DecisionKind.NO_TRADE,
            reasons=gate_reasons,
            counterfactual_class=_gate_counterfactual_class(candidate),
        )
    return _decision(
        policy=policy,
        candidate=candidate,
        playbook=playbook,
        decision=DecisionKind.ENTER_SHADOW,
        reasons=(ReasonCode.ALL_GATES_PASS,),
        counterfactual_class=CounterfactualClass.POLICY_EVALUABLE,
    )


def _capacity_rejection(decision: RouterDecision, reason: ReasonCode) -> RouterDecision:
    return replace(
        decision,
        decision=DecisionKind.NO_TRADE,
        reason_codes=(reason,),
        counterfactual_class=CounterfactualClass.POLICY_EVALUABLE,
    )


def route_batch(
    policy: RouterPolicy,
    population: PopulationSnapshot,
    candidates: Iterable[CandidateEvaluation],
) -> tuple[RouterDecision, ...]:
    """Evaluate a cohort and apply preregistered deterministic capacity resolution."""

    candidate_rows = tuple(candidates)
    decisions = [evaluate_candidate(policy, population, candidate) for candidate in candidate_rows]
    eligible = [
        decision for decision in decisions if decision.decision is DecisionKind.ENTER_SHADOW
    ]
    if len(eligible) <= policy.max_entries:
        return tuple(sorted(decisions, key=lambda decision: decision.decision_id))

    if policy.capacity_rule is None:
        rejected = {
            decision.decision_id: _capacity_rejection(
                decision,
                ReasonCode.CAPACITY_RULE_MISSING,
            )
            for decision in eligible
        }
        return tuple(
            sorted(
                (rejected.get(decision.decision_id, decision) for decision in decisions),
                key=lambda decision: decision.decision_id,
            )
        )

    rule = policy.capacity_rule
    playbook_ranks = {playbook_id: rank for rank, playbook_id in enumerate(rule.playbook_priority)}
    if any(
        decision.playbook_id is None or decision.playbook_id not in playbook_ranks
        for decision in eligible
    ):
        rejected = {
            decision.decision_id: _capacity_rejection(
                decision,
                ReasonCode.CAPACITY_PRIORITY_MISSING,
            )
            for decision in eligible
        }
        return tuple(
            sorted(
                (rejected.get(decision.decision_id, decision) for decision in decisions),
                key=lambda decision: decision.decision_id,
            )
        )

    ordered = sorted(
        eligible,
        key=lambda decision: (
            playbook_ranks[decision.playbook_id or ""],
            decision.candidate_priority,
            decision.candidate_id,
        ),
    )
    accepted_ids: set[str] = set()
    instrument_counts: Counter[str] = Counter()
    sector_counts: Counter[str] = Counter()
    for decision in ordered:
        if len(accepted_ids) >= policy.max_entries:
            continue
        if instrument_counts[decision.instrument] >= rule.same_instrument_limit:
            continue
        if (
            rule.same_sector_limit is not None
            and decision.sector is not None
            and sector_counts[decision.sector] >= rule.same_sector_limit
        ):
            continue
        accepted_ids.add(decision.decision_id)
        instrument_counts[decision.instrument] += 1
        if decision.sector is not None:
            sector_counts[decision.sector] += 1

    resolved = [
        decision
        if decision.decision is not DecisionKind.ENTER_SHADOW
        or decision.decision_id in accepted_ids
        else _capacity_rejection(decision, ReasonCode.CAPACITY_REJECTED)
        for decision in decisions
    ]
    return tuple(sorted(resolved, key=lambda decision: decision.decision_id))
