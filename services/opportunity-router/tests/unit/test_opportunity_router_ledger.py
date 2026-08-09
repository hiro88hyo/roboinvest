from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from opportunity_router._testing import make_candidate, make_policy, make_population
from opportunity_router.core import evaluate_candidate
from opportunity_router.ledger import DecisionLedger, LedgerConflictError, LedgerIntegrityError
from opportunity_router.models import ReasonCode


def test_append_and_read_hash_chained_records(tmp_path) -> None:  # type: ignore[no-untyped-def]
    population = make_population(
        candidate_ids=("candidate-1", "candidate-2"),
        instruments=("7203", "6758"),
    )
    policy = make_policy(max_entries=2)
    decisions = (
        evaluate_candidate(
            policy,
            population,
            make_candidate(population, candidate_id="candidate-1", instrument="7203"),
        ),
        evaluate_candidate(
            policy,
            population,
            make_candidate(population, candidate_id="candidate-2", instrument="6758"),
        ),
    )
    ledger = DecisionLedger(tmp_path / "nested" / "decisions.jsonl")

    appended = ledger.append_all(decisions)
    reread = ledger.read()

    assert reread == appended
    assert [record.sequence for record in reread] == [1, 2]
    assert reread[0].previous_record_sha256 is None
    assert reread[1].previous_record_sha256 == reread[0].record_sha256
    assert reread[0].decision == decisions[0].to_dict()


def test_repeated_identical_decision_is_idempotent(tmp_path) -> None:  # type: ignore[no-untyped-def]
    population = make_population()
    decision = evaluate_candidate(make_policy(), population, make_candidate(population))
    ledger = DecisionLedger(tmp_path / "decisions.jsonl")

    first = ledger.append(decision)
    second = ledger.append(decision)

    assert first == second
    assert len(ledger.read()) == 1
    assert len(ledger.path.read_text(encoding="utf-8").splitlines()) == 1


def test_same_decision_id_with_different_content_is_rejected(tmp_path) -> None:  # type: ignore[no-untyped-def]
    population = make_population()
    decision = evaluate_candidate(make_policy(), population, make_candidate(population))
    conflicting = replace(decision, reason_codes=(ReasonCode.CONTEXT_FAIL,))
    ledger = DecisionLedger(tmp_path / "decisions.jsonl")
    ledger.append(decision)

    with pytest.raises(LedgerConflictError, match=decision.decision_id):
        ledger.append(conflicting)

    assert len(ledger.read()) == 1


def test_tampered_decision_is_detected_before_append(tmp_path) -> None:  # type: ignore[no-untyped-def]
    population = make_population()
    decision = evaluate_candidate(make_policy(), population, make_candidate(population))
    ledger = DecisionLedger(tmp_path / "decisions.jsonl")
    ledger.append(decision)

    row = json.loads(ledger.path.read_text(encoding="utf-8"))
    row["decision"]["instrument"] = "9984"
    ledger.path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    with pytest.raises(LedgerIntegrityError, match="hash mismatch"):
        ledger.read()
    with pytest.raises(LedgerIntegrityError, match="hash mismatch"):
        ledger.append(decision)


def test_broken_chain_and_blank_rows_are_rejected(tmp_path) -> None:  # type: ignore[no-untyped-def]
    population = make_population(
        candidate_ids=("candidate-1", "candidate-2"),
        instruments=("7203", "6758"),
    )
    policy = make_policy(max_entries=2)
    ledger = DecisionLedger(tmp_path / "decisions.jsonl")
    ledger.append_all(
        (
            evaluate_candidate(
                policy,
                population,
                make_candidate(population, candidate_id="candidate-1", instrument="7203"),
            ),
            evaluate_candidate(
                policy,
                population,
                make_candidate(population, candidate_id="candidate-2", instrument="6758"),
            ),
        )
    )
    lines = ledger.path.read_text(encoding="utf-8").splitlines()
    second = json.loads(lines[1])
    second["previous_record_sha256"] = None
    ledger.path.write_text(f"{lines[0]}\n{json.dumps(second)}\n", encoding="utf-8")

    with pytest.raises(LedgerIntegrityError, match="chain mismatch"):
        ledger.read()

    blank_ledger = DecisionLedger(tmp_path / "blank.jsonl")
    blank_ledger.path.write_text("\n", encoding="utf-8")
    with pytest.raises(LedgerIntegrityError, match="blank ledger line"):
        blank_ledger.read()


def test_empty_append_does_not_create_file(tmp_path) -> None:  # type: ignore[no-untyped-def]
    ledger = DecisionLedger(tmp_path / "decisions.jsonl")

    assert ledger.append_all(()) == ()
    assert ledger.read() == ()
    assert not ledger.path.exists()


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("not-json\n", "invalid JSON"),
        ("[]\n", "not an object"),
        ('{"sequence":true}\n', "invalid sequence"),
    ],
)
def test_malformed_existing_ledger_is_rejected(
    tmp_path: Path,
    content: str,
    expected: str,
) -> None:
    ledger = DecisionLedger(tmp_path / "malformed.jsonl")
    ledger.path.write_text(content, encoding="utf-8")

    with pytest.raises(LedgerIntegrityError, match=expected):
        ledger.read()
