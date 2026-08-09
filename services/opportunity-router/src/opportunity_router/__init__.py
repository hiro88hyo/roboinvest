"""Phase 1 policy-authorized opportunity router pure library."""

from opportunity_router.core import evaluate_candidate, route_batch
from opportunity_router.integrity import canonical_json, canonical_sha256
from opportunity_router.ledger import DecisionLedger, LedgerConflictError, LedgerIntegrityError
from opportunity_router.models import (
    CandidateEvaluation,
    CandidateIntakeRegistration,
    CapacityRule,
    CounterfactualClass,
    DecisionKind,
    GateResults,
    GateState,
    PlaybookRegistration,
    PopulationSnapshot,
    ReasonCode,
    RouterDecision,
    RouterPolicy,
)

__all__ = [
    "CandidateEvaluation",
    "CandidateIntakeRegistration",
    "CapacityRule",
    "CounterfactualClass",
    "DecisionKind",
    "DecisionLedger",
    "GateResults",
    "GateState",
    "LedgerConflictError",
    "LedgerIntegrityError",
    "PlaybookRegistration",
    "PopulationSnapshot",
    "ReasonCode",
    "RouterDecision",
    "RouterPolicy",
    "canonical_json",
    "canonical_sha256",
    "evaluate_candidate",
    "route_batch",
]
