"""Immutable internal models for the Phase 1 opportunity router."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Self

from opportunity_router.integrity import (
    canonical_json,
    canonical_sha256,
    verify_canonical_payload,
)

MAX_ACTIVE_PLAYBOOKS = 3


class GateState(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class DecisionKind(StrEnum):
    ENTER_SHADOW = "ENTER_SHADOW"
    NO_TRADE = "NO_TRADE"
    EXPIRED = "EXPIRED"
    DUPLICATE = "DUPLICATE"
    POLICY_DISABLED = "POLICY_DISABLED"


class CounterfactualClass(StrEnum):
    POLICY_EVALUABLE = "POLICY_EVALUABLE"
    ECONOMIC_ONLY_NOT_EXECUTABLE = "ECONOMIC_ONLY_NOT_EXECUTABLE"
    ADMINISTRATIVE_TERMINAL = "ADMINISTRATIVE_TERMINAL"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ReasonCode(StrEnum):
    ALL_GATES_PASS = "ALL_GATES_PASS"
    POLICY_DISABLED = "POLICY_DISABLED"
    POLICY_NOT_EFFECTIVE = "POLICY_NOT_EFFECTIVE"
    POLICY_EXPIRED = "POLICY_EXPIRED"
    POLICY_HASH_MISMATCH = "POLICY_HASH_MISMATCH"
    PLAYBOOK_HASH_MISMATCH = "PLAYBOOK_HASH_MISMATCH"
    NO_ADMITTED_PLAYBOOKS = "NO_ADMITTED_PLAYBOOKS"
    INTAKE_CONTRACT_HASH_MISMATCH = "INTAKE_CONTRACT_HASH_MISMATCH"
    INTAKE_VERSION_MISMATCH = "INTAKE_VERSION_MISMATCH"
    POPULATION_HASH_MISMATCH = "POPULATION_HASH_MISMATCH"
    CANDIDATE_NOT_IN_POPULATION = "CANDIDATE_NOT_IN_POPULATION"
    CANDIDATE_EXPIRED = "CANDIDATE_EXPIRED"
    EVIDENCE_CUTOFF_AFTER_DECISION = "EVIDENCE_CUTOFF_AFTER_DECISION"
    DUPLICATE_CANDIDATE = "DUPLICATE_CANDIDATE"
    NO_PLAYBOOK_MATCH = "NO_PLAYBOOK_MATCH"
    AMBIGUOUS_PLAYBOOK = "AMBIGUOUS_PLAYBOOK"
    PLAYBOOK_NOT_ADMITTED = "PLAYBOOK_NOT_ADMITTED"
    PLAYBOOK_DISABLED = "PLAYBOOK_DISABLED"
    EVIDENCE_FAIL = "EVIDENCE_FAIL"
    EVIDENCE_UNKNOWN = "EVIDENCE_UNKNOWN"
    MECHANISM_FAIL = "MECHANISM_FAIL"
    MECHANISM_UNKNOWN = "MECHANISM_UNKNOWN"
    CONTEXT_FAIL = "CONTEXT_FAIL"
    CONTEXT_UNKNOWN = "CONTEXT_UNKNOWN"
    EXECUTION_FAIL = "EXECUTION_FAIL"
    EXECUTION_UNKNOWN = "EXECUTION_UNKNOWN"
    PORTFOLIO_FAIL = "PORTFOLIO_FAIL"
    PORTFOLIO_UNKNOWN = "PORTFOLIO_UNKNOWN"
    CAPACITY_RULE_MISSING = "CAPACITY_RULE_MISSING"
    CAPACITY_PRIORITY_MISSING = "CAPACITY_PRIORITY_MISSING"
    CAPACITY_REJECTED = "CAPACITY_REJECTED"


def _require_text(value: str, field: str) -> None:
    if not value.strip():
        raise ValueError(f"{field} must not be blank")


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class GateResults:
    evidence: GateState
    mechanism: GateState
    context: GateState
    execution: GateState
    portfolio_precheck: GateState

    def all_pass(self) -> bool:
        return all(state is GateState.PASS for state in self.as_dict().values())

    def as_dict(self) -> dict[str, GateState]:
        return {
            "evidence": self.evidence,
            "mechanism": self.mechanism,
            "context": self.context,
            "execution": self.execution,
            "portfolio_precheck": self.portfolio_precheck,
        }

    def to_dict(self) -> dict[str, str]:
        return {name: state.value for name, state in self.as_dict().items()}


@dataclass(frozen=True, slots=True)
class CandidateIntakeRegistration:
    version: str
    contract_json: str
    declared_sha256: str

    def __post_init__(self) -> None:
        _require_text(self.version, "version")
        _require_text(self.contract_json, "contract_json")
        _require_text(self.declared_sha256, "declared_sha256")

    @classmethod
    def build(cls, *, version: str, contract: object) -> Self:
        payload_json = canonical_json(contract)
        return cls(
            version=version,
            contract_json=payload_json,
            declared_sha256=canonical_sha256(contract),
        )

    def verify_integrity(self) -> bool:
        return verify_canonical_payload(self.contract_json, self.declared_sha256)

    def binding_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "declared_sha256": self.declared_sha256,
        }


@dataclass(frozen=True, slots=True)
class PlaybookRegistration:
    playbook_id: str
    version: str
    contract_json: str
    declared_sha256: str
    enabled: bool = True

    def __post_init__(self) -> None:
        _require_text(self.playbook_id, "playbook_id")
        _require_text(self.version, "version")
        _require_text(self.contract_json, "contract_json")
        _require_text(self.declared_sha256, "declared_sha256")

    @classmethod
    def build(
        cls,
        *,
        playbook_id: str,
        version: str,
        contract: object,
        enabled: bool = True,
    ) -> Self:
        payload_json = canonical_json(contract)
        return cls(
            playbook_id=playbook_id,
            version=version,
            contract_json=payload_json,
            declared_sha256=canonical_sha256(contract),
            enabled=enabled,
        )

    def verify_integrity(self) -> bool:
        return verify_canonical_payload(self.contract_json, self.declared_sha256)

    def binding_dict(self) -> dict[str, object]:
        return {
            "playbook_id": self.playbook_id,
            "version": self.version,
            "declared_sha256": self.declared_sha256,
            "enabled": self.enabled,
        }


@dataclass(frozen=True, slots=True)
class CapacityRule:
    rule_id: str
    version: str
    playbook_priority: tuple[str, ...]
    same_instrument_limit: int = 1
    same_sector_limit: int | None = None
    tie_breaker: str = "CANDIDATE_ID_ASC"

    def __post_init__(self) -> None:
        _require_text(self.rule_id, "rule_id")
        _require_text(self.version, "version")
        if not self.playbook_priority:
            raise ValueError("playbook_priority must not be empty")
        if len(set(self.playbook_priority)) != len(self.playbook_priority):
            raise ValueError("playbook_priority must contain unique IDs")
        if self.same_instrument_limit < 1:
            raise ValueError("same_instrument_limit must be positive")
        if self.same_sector_limit is not None and self.same_sector_limit < 1:
            raise ValueError("same_sector_limit must be positive when set")
        if self.tie_breaker != "CANDIDATE_ID_ASC":
            raise ValueError("Phase 1 only supports CANDIDATE_ID_ASC tie breaking")

    def to_dict(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "version": self.version,
            "playbook_priority": list(self.playbook_priority),
            "same_instrument_limit": self.same_instrument_limit,
            "same_sector_limit": self.same_sector_limit,
            "tie_breaker": self.tie_breaker,
            "candidate_priority": "ASCENDING",
        }


@dataclass(frozen=True, slots=True)
class RouterPolicy:
    policy_id: str
    version: str
    effective_at: datetime
    expires_at: datetime
    candidate_intake: CandidateIntakeRegistration
    playbooks: tuple[PlaybookRegistration, ...]
    max_entries: int
    capacity_rule: CapacityRule | None
    enabled: bool
    declared_sha256: str

    def __post_init__(self) -> None:
        _require_text(self.policy_id, "policy_id")
        _require_text(self.version, "version")
        _require_text(self.declared_sha256, "declared_sha256")
        _require_aware(self.effective_at, "effective_at")
        _require_aware(self.expires_at, "expires_at")
        if self.expires_at <= self.effective_at:
            raise ValueError("expires_at must be after effective_at")
        if self.max_entries < 1:
            raise ValueError("max_entries must be positive")
        ids = [playbook.playbook_id for playbook in self.playbooks]
        if len(set(ids)) != len(ids):
            raise ValueError("playbook IDs must be unique")
        if len(self.playbooks) > MAX_ACTIVE_PLAYBOOKS:
            raise ValueError(f"at most {MAX_ACTIVE_PLAYBOOKS} playbooks may be registered")

    @classmethod
    def build(
        cls,
        *,
        policy_id: str,
        version: str,
        effective_at: datetime,
        expires_at: datetime,
        candidate_intake: CandidateIntakeRegistration,
        playbooks: tuple[PlaybookRegistration, ...] = (),
        max_entries: int = 1,
        capacity_rule: CapacityRule | None = None,
        enabled: bool = True,
    ) -> Self:
        payload = cls.binding_payload_for(
            policy_id=policy_id,
            version=version,
            effective_at=effective_at,
            expires_at=expires_at,
            candidate_intake=candidate_intake,
            playbooks=playbooks,
            max_entries=max_entries,
            capacity_rule=capacity_rule,
            enabled=enabled,
        )
        return cls(
            policy_id=policy_id,
            version=version,
            effective_at=effective_at,
            expires_at=expires_at,
            candidate_intake=candidate_intake,
            playbooks=playbooks,
            max_entries=max_entries,
            capacity_rule=capacity_rule,
            enabled=enabled,
            declared_sha256=canonical_sha256(payload),
        )

    @staticmethod
    def binding_payload_for(
        *,
        policy_id: str,
        version: str,
        effective_at: datetime,
        expires_at: datetime,
        candidate_intake: CandidateIntakeRegistration,
        playbooks: tuple[PlaybookRegistration, ...],
        max_entries: int,
        capacity_rule: CapacityRule | None,
        enabled: bool,
    ) -> dict[str, object]:
        return {
            "policy_id": policy_id,
            "version": version,
            "effective_at": effective_at.isoformat(),
            "expires_at": expires_at.isoformat(),
            "candidate_intake": candidate_intake.binding_dict(),
            "playbooks": [playbook.binding_dict() for playbook in playbooks],
            "max_entries": max_entries,
            "capacity_rule": capacity_rule.to_dict() if capacity_rule is not None else None,
            "enabled": enabled,
        }

    def binding_payload(self) -> dict[str, object]:
        return self.binding_payload_for(
            policy_id=self.policy_id,
            version=self.version,
            effective_at=self.effective_at,
            expires_at=self.expires_at,
            candidate_intake=self.candidate_intake,
            playbooks=self.playbooks,
            max_entries=self.max_entries,
            capacity_rule=self.capacity_rule,
            enabled=self.enabled,
        )

    def verify_integrity(self) -> bool:
        return canonical_sha256(self.binding_payload()) == self.declared_sha256

    @property
    def candidate_intake_version(self) -> str:
        return self.candidate_intake.version

    def playbook_by_id(self, playbook_id: str) -> PlaybookRegistration | None:
        return next(
            (playbook for playbook in self.playbooks if playbook.playbook_id == playbook_id),
            None,
        )


@dataclass(frozen=True, slots=True)
class PopulationSnapshot:
    intake_version: str
    intake_contract_sha256: str
    session_id: str
    eligible_instruments: tuple[str, ...]
    candidate_ids: tuple[str, ...]
    declared_sha256: str

    def __post_init__(self) -> None:
        _require_text(self.intake_version, "intake_version")
        _require_text(self.intake_contract_sha256, "intake_contract_sha256")
        _require_text(self.session_id, "session_id")
        _require_text(self.declared_sha256, "declared_sha256")
        if len(set(self.eligible_instruments)) != len(self.eligible_instruments):
            raise ValueError("eligible_instruments must be unique")
        if len(set(self.candidate_ids)) != len(self.candidate_ids):
            raise ValueError("candidate_ids must be unique")

    @classmethod
    def build(
        cls,
        *,
        intake_version: str,
        intake_contract_sha256: str,
        session_id: str,
        eligible_instruments: tuple[str, ...],
        candidate_ids: tuple[str, ...],
    ) -> Self:
        instruments = tuple(sorted(eligible_instruments))
        candidates = tuple(sorted(candidate_ids))
        payload = cls.binding_payload_for(
            intake_version=intake_version,
            intake_contract_sha256=intake_contract_sha256,
            session_id=session_id,
            eligible_instruments=instruments,
            candidate_ids=candidates,
        )
        return cls(
            intake_version=intake_version,
            intake_contract_sha256=intake_contract_sha256,
            session_id=session_id,
            eligible_instruments=instruments,
            candidate_ids=candidates,
            declared_sha256=canonical_sha256(payload),
        )

    @staticmethod
    def binding_payload_for(
        *,
        intake_version: str,
        intake_contract_sha256: str,
        session_id: str,
        eligible_instruments: tuple[str, ...],
        candidate_ids: tuple[str, ...],
    ) -> dict[str, object]:
        return {
            "intake_version": intake_version,
            "intake_contract_sha256": intake_contract_sha256,
            "session_id": session_id,
            "eligible_instruments": list(eligible_instruments),
            "candidate_ids": list(candidate_ids),
        }

    def binding_payload(self) -> dict[str, object]:
        return self.binding_payload_for(
            intake_version=self.intake_version,
            intake_contract_sha256=self.intake_contract_sha256,
            session_id=self.session_id,
            eligible_instruments=self.eligible_instruments,
            candidate_ids=self.candidate_ids,
        )

    def verify_integrity(self) -> bool:
        return canonical_sha256(self.binding_payload()) == self.declared_sha256


@dataclass(frozen=True, slots=True)
class CandidateEvaluation:
    candidate_id: str
    instrument: str
    sector: str | None
    decision_at: datetime
    evidence_cutoff_at: datetime
    valid_until: datetime
    candidate_intake_version: str
    upstream_population_hash: str
    matched_playbook_ids: tuple[str, ...]
    gates: GateResults
    candidate_priority: int = 0
    duplicate_of_candidate_id: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.candidate_id, "candidate_id")
        _require_text(self.instrument, "instrument")
        _require_text(self.candidate_intake_version, "candidate_intake_version")
        _require_text(self.upstream_population_hash, "upstream_population_hash")
        _require_aware(self.decision_at, "decision_at")
        _require_aware(self.evidence_cutoff_at, "evidence_cutoff_at")
        _require_aware(self.valid_until, "valid_until")
        if len(set(self.matched_playbook_ids)) != len(self.matched_playbook_ids):
            raise ValueError("matched_playbook_ids must be unique")
        if self.duplicate_of_candidate_id is not None:
            _require_text(self.duplicate_of_candidate_id, "duplicate_of_candidate_id")


@dataclass(frozen=True, slots=True)
class RouterDecision:
    decision_id: str
    decision_at: datetime
    policy_id: str
    policy_version: str
    policy_sha256: str
    playbook_id: str | None
    playbook_version: str | None
    playbook_contract_sha256: str | None
    candidate_id: str
    candidate_intake_version: str
    candidate_intake_contract_sha256: str
    upstream_population_hash: str
    instrument: str
    sector: str | None
    evidence_cutoff_at: datetime
    valid_until: datetime
    matched_playbook_ids: tuple[str, ...]
    assignment_rule_version: str
    capacity_rule_id: str | None
    capacity_rule_version: str | None
    gates: GateResults
    decision: DecisionKind
    reason_codes: tuple[ReasonCode, ...]
    counterfactual_class: CounterfactualClass
    candidate_priority: int

    def to_dict(self) -> dict[str, object]:
        return {
            "decision_id": self.decision_id,
            "decision_at": self.decision_at.isoformat(),
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "policy_sha256": self.policy_sha256,
            "playbook_id": self.playbook_id,
            "playbook_version": self.playbook_version,
            "playbook_contract_sha256": self.playbook_contract_sha256,
            "candidate_id": self.candidate_id,
            "candidate_intake_version": self.candidate_intake_version,
            "candidate_intake_contract_sha256": self.candidate_intake_contract_sha256,
            "upstream_population_hash": self.upstream_population_hash,
            "instrument": self.instrument,
            "sector": self.sector,
            "evidence_cutoff_at": self.evidence_cutoff_at.isoformat(),
            "valid_until": self.valid_until.isoformat(),
            "matched_playbook_ids": list(self.matched_playbook_ids),
            "assignment_rule_version": self.assignment_rule_version,
            "capacity_rule_id": self.capacity_rule_id,
            "capacity_rule_version": self.capacity_rule_version,
            "gates": self.gates.to_dict(),
            "decision": self.decision.value,
            "reason_codes": [reason.value for reason in self.reason_codes],
            "counterfactual_class": self.counterfactual_class.value,
            "candidate_priority": self.candidate_priority,
        }
