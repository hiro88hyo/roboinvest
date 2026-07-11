from __future__ import annotations

import json
from pathlib import Path

import pytest
from strategy_rule.event_paper._testing import (
    TARGET_DATE,
    make_event_artifact_payload,
    make_event_candidate,
)
from strategy_rule.event_paper.artifact import (
    EventArtifactError,
    load_event_paper_artifact,
)


def _write(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_load_event_artifact_accepts_causal_v2_and_hashes_exact_bytes(tmp_path: Path) -> None:
    path = tmp_path / "candidates.json"
    _write(path, make_event_artifact_payload())

    loaded = load_event_paper_artifact(path)

    assert loaded.artifact.schema_version == 2
    assert loaded.artifact.candidates[0].execution_candidate_id == "cluster-7203:obs-7203"
    assert len(loaded.sha256) == 64
    loaded.artifact.validate_target_date(TARGET_DATE)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update(causality_verified=False),
        lambda payload: payload["rule"].update(catastrophic_stop_pct="-0.20"),
        lambda payload: payload["candidates"][0].update(entry_price_assumption="1000"),
        lambda payload: payload["candidates"][0].update(
            execution_candidate_id="the-strategy-definition-is-not-an-occurrence"
        ),
        lambda payload: payload["candidates"][0].update(min_forecast_per="15.01"),
        lambda payload: payload["candidates"][0].update(event_ids=["event-dividend"]),
        lambda payload: payload["candidates"][0].update(entry_date="2026-01-20"),
        lambda payload: payload["candidates"][0].update(entry_date="2026-01-22"),
        lambda payload: payload["candidates"][0].update(valuation_reference_bar_date="2026-01-21"),
        lambda payload: payload["candidates"][0].update(
            valuation_reference_available_at="2026-01-20T07:00:00+00:00"
        ),
        lambda payload: payload["candidates"][0].update(
            source_received_at="2026-01-20T05:00:00+00:00"
        ),
    ],
)
def test_load_event_artifact_rejects_unsafe_or_drifted_payload(
    tmp_path: Path,
    mutate: object,
) -> None:
    payload = make_event_artifact_payload()
    mutate(payload)  # type: ignore[operator]
    path = tmp_path / "unsafe.json"
    _write(path, payload)

    with pytest.raises(EventArtifactError):
        load_event_paper_artifact(path)


def test_artifact_rejects_duplicate_symbols_even_with_distinct_occurrences(tmp_path: Path) -> None:
    second = make_event_candidate(
        cluster_id="cluster-2",
        observation_id="obs-2",
        execution_candidate_id="cluster-2:obs-2",
    )
    path = tmp_path / "duplicates.json"
    _write(path, make_event_artifact_payload(candidates=[make_event_candidate(), second]))

    with pytest.raises(EventArtifactError, match="multiple event candidates"):
        load_event_paper_artifact(path)


def test_artifact_target_date_is_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "candidates.json"
    _write(path, make_event_artifact_payload())
    artifact = load_event_paper_artifact(path).artifact

    with pytest.raises(EventArtifactError, match="entry_date"):
        artifact.validate_target_date(TARGET_DATE.replace(day=22))


def test_incomplete_feature_data_is_reportable_but_not_publishable(tmp_path: Path) -> None:
    candidate = make_event_candidate(
        feature_data_complete=False,
        valuation_reference_bar_date="2026-01-19",
        valuation_reference_available_at="2026-01-19T06:30:00+00:00",
    )
    payload = make_event_artifact_payload(candidates=[candidate])
    payload["summary"]["missing_signal_date_ohlcv_count"] = 1
    path = tmp_path / "incomplete-feature-data.json"
    _write(path, payload)

    artifact = load_event_paper_artifact(path).artifact

    assert artifact.candidates[0].feature_data_complete is False
    with pytest.raises(EventArtifactError, match="feature data is incomplete"):
        artifact.validate_target_date(TARGET_DATE)


def test_artifact_recomputes_feature_data_completeness_from_lineage(tmp_path: Path) -> None:
    candidate = make_event_candidate(
        feature_data_complete=True,
        valuation_reference_bar_date="2026-01-19",
        valuation_reference_available_at="2026-01-19T06:30:00+00:00",
    )
    path = tmp_path / "forged-feature-completeness.json"
    _write(path, make_event_artifact_payload(candidates=[candidate]))

    with pytest.raises(EventArtifactError, match="feature_data_complete"):
        load_event_paper_artifact(path)


def test_artifact_recomputes_daily_bar_availability(tmp_path: Path) -> None:
    candidate = make_event_candidate(
        valuation_reference_available_at="2026-01-20T05:30:00+00:00",
    )
    path = tmp_path / "forged-bar-availability.json"
    _write(path, make_event_artifact_payload(candidates=[candidate]))

    with pytest.raises(EventArtifactError, match="availability does not match"):
        load_event_paper_artifact(path)


def test_artifact_rejects_feature_cutoff_from_another_signal_date(tmp_path: Path) -> None:
    candidate = make_event_candidate(
        feature_cutoff_at="2026-01-19T06:30:00+00:00",
        data_available_at="2026-01-19T06:30:00+00:00",
        valuation_reference_bar_date="2026-01-19",
        valuation_reference_available_at="2026-01-19T06:30:00+00:00",
    )
    path = tmp_path / "wrong-cutoff-date.json"
    _write(path, make_event_artifact_payload(candidates=[candidate]))

    with pytest.raises(EventArtifactError, match="feature cutoff date"):
        load_event_paper_artifact(path)


def test_zero_candidate_artifact_is_reportable_but_not_publishable(tmp_path: Path) -> None:
    path = tmp_path / "zero-candidates.json"
    _write(path, make_event_artifact_payload(candidates=[]))

    artifact = load_event_paper_artifact(path).artifact

    assert artifact.candidates == []
    with pytest.raises(EventArtifactError, match="no candidates"):
        artifact.validate_target_date(TARGET_DATE)


@pytest.mark.parametrize(
    "fetched_at",
    [
        "2026-01-20T14:59:59+00:00",
        "2026-01-21T00:00:00+00:00",
    ],
)
def test_artifact_recomputes_causal_fetch_window(
    tmp_path: Path,
    fetched_at: str,
) -> None:
    payload = make_event_artifact_payload(fetched_at=fetched_at)
    payload["candidates"][0].update(
        source_received_at=fetched_at,
    )
    path = tmp_path / "bad-window.json"
    _write(path, payload)

    with pytest.raises(EventArtifactError, match="causal coverage window"):
        load_event_paper_artifact(path)


def test_artifact_rejects_exchange_holiday_as_entry_date(tmp_path: Path) -> None:
    fetched_at = "2026-04-28T15:30:00+00:00"
    candidate = make_event_candidate(
        signal_date="2026-04-28",
        entry_date="2026-04-29",
        feature_cutoff_at="2026-04-28T06:30:00+00:00",
        data_available_at="2026-04-28T06:30:00+00:00",
        source_received_at=fetched_at,
        valuation_reference_bar_date="2026-04-28",
        valuation_reference_available_at="2026-04-28T06:30:00+00:00",
    )
    payload = make_event_artifact_payload(
        signal_date="2026-04-28",
        fetched_at=fetched_at,
        candidates=[candidate],
    )
    path = tmp_path / "holiday-entry.json"
    _write(path, payload)

    with pytest.raises(EventArtifactError, match="next TSE business day"):
        load_event_paper_artifact(path)


def test_artifact_preserves_disclosure_time_feature_vintage(tmp_path: Path) -> None:
    candidate = make_event_candidate(
        feature_cutoff_at="2026-01-20T05:30:00+00:00",
        data_available_at="2026-01-20T05:30:00+00:00",
        valuation_reference_bar_date="2026-01-19",
        valuation_reference_available_at="2026-01-19T06:30:00+00:00",
    )
    path = tmp_path / "prior-close-vintage.json"
    _write(path, make_event_artifact_payload(candidates=[candidate]))

    artifact = load_event_paper_artifact(path).artifact

    assert artifact.candidates[0].valuation_reference_bar_date.isoformat() == "2026-01-19"
    assert artifact.candidates[0].feature_cutoff_at < artifact.candidates[0].source_received_at


@pytest.mark.parametrize("signal_date", ["2026-01-18", "2026-02-11"])
def test_artifact_rejects_non_business_signal_date(
    tmp_path: Path,
    signal_date: str,
) -> None:
    path = tmp_path / "invalid-signal-date.json"
    _write(
        path,
        make_event_artifact_payload(
            signal_date=signal_date,
            fetched_at="2026-02-12T00:00:00+00:00",
            candidates=[],
        ),
    )

    with pytest.raises(EventArtifactError, match="signal_date is not a TSE business day"):
        load_event_paper_artifact(path)
