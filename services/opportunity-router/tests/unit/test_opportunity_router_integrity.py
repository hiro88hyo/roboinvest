from __future__ import annotations

import pytest
from opportunity_router.integrity import canonical_json, canonical_sha256, verify_canonical_payload
from opportunity_router.models import CapacityRule, PopulationSnapshot


def test_canonical_json_is_order_independent_and_rejects_nan() -> None:
    assert canonical_json({"b": 2, "a": 1}) == '{"a":1,"b":2}'
    assert canonical_sha256({"a": 1, "b": 2}) == canonical_sha256({"b": 2, "a": 1})
    with pytest.raises(ValueError, match="Out of range float values"):
        canonical_json({"bad": float("nan")})


def test_verify_canonical_payload_rejects_invalid_noncanonical_and_wrong_hash() -> None:
    canonical = canonical_json({"a": 1})
    digest = canonical_sha256({"a": 1})

    assert verify_canonical_payload(canonical, digest)
    assert not verify_canonical_payload("not-json", digest)
    assert not verify_canonical_payload('{"a": 1}', digest)
    assert not verify_canonical_payload(canonical, "0" * 64)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"playbook_priority": ()},
        {"playbook_priority": ("a", "a")},
        {"playbook_priority": ("a",), "same_instrument_limit": 0},
        {"playbook_priority": ("a",), "same_sector_limit": 0},
        {"playbook_priority": ("a",), "tie_breaker": "ARRIVAL_ORDER"},
    ],
)
def test_capacity_rule_rejects_unsafe_configuration(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        CapacityRule(rule_id="capacity", version="1", **kwargs)  # type: ignore[arg-type]


def test_population_snapshot_rejects_duplicate_population_members() -> None:
    with pytest.raises(ValueError, match="eligible_instruments must be unique"):
        PopulationSnapshot.build(
            intake_version="v1",
            intake_contract_sha256="a" * 64,
            session_id="session",
            eligible_instruments=("7203", "7203"),
            candidate_ids=("candidate",),
        )
    with pytest.raises(ValueError, match="candidate_ids must be unique"):
        PopulationSnapshot.build(
            intake_version="v1",
            intake_contract_sha256="a" * 64,
            session_id="session",
            eligible_instruments=("7203",),
            candidate_ids=("candidate", "candidate"),
        )
