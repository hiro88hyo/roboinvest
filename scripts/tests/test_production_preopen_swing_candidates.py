from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

from strategy_rule.event_paper._testing import (
    make_event_artifact_payload,
    make_event_candidate,
)


def _load_module():
    path = Path(__file__).resolve().parents[1] / "production-preopen-check.py"
    spec = importlib.util.spec_from_file_location("production_preopen_check", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


preopen = _load_module()


def _args(path: Path) -> argparse.Namespace:
    return argparse.Namespace(
        swing_candidates_json=path,
        require_swing_candidates=False,
        target_date=date(2026, 1, 22),
    )


def _write_payload(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _causal_payload() -> dict[str, object]:
    candidate = make_event_candidate(
        signal_date="2026-01-21",
        entry_date="2026-01-22",
        feature_cutoff_at="2026-01-21T06:30:00+00:00",
        data_available_at="2026-01-21T06:30:00+00:00",
        source_received_at="2026-01-21T15:30:00+00:00",
        valuation_reference_bar_date="2026-01-21",
        valuation_reference_available_at="2026-01-21T06:30:00+00:00",
    )
    return make_event_artifact_payload(
        signal_date="2026-01-21",
        fetched_at="2026-01-21T15:30:00+00:00",
        candidates=[candidate],
    )


def test_preopen_accepts_causal_dry_run_candidate_artifact(tmp_path: Path) -> None:
    path = tmp_path / "candidates.json"
    _write_payload(path, _causal_payload())
    reporter = preopen.Reporter(quiet=True)

    preopen.check_swing_paper_candidates(reporter, _args(path))

    assert reporter.counts["NG"] == 0


def test_preopen_rejects_legacy_future_price_artifact(tmp_path: Path) -> None:
    path = tmp_path / "legacy-candidates.json"
    payload = _causal_payload()
    payload.pop("causality_verified")
    payload.pop("causality")
    payload["mode"] = "paper_publish"
    payload["summary"] = {"candidate_count": 1, "published_count": 1}
    candidate = payload["candidates"][0]
    candidate["entry_price_assumption"] = "1022"
    candidate["stop_loss_price"] = "919.80"
    _write_payload(path, payload)
    reporter = preopen.Reporter(quiet=True)

    preopen.check_swing_paper_candidates(reporter, _args(path))

    assert reporter.counts["NG"] >= 1


def test_preopen_reuses_strict_frozen_rule_contract(tmp_path: Path) -> None:
    path = tmp_path / "drifted-rule-candidates.json"
    payload = _causal_payload()
    payload["rule"]["forecast_per_threshold"] = "16"
    _write_payload(path, payload)
    reporter = preopen.Reporter(quiet=True)

    preopen.check_swing_paper_candidates(reporter, _args(path))

    assert reporter.counts["NG"] == 1


def test_preopen_rejects_stale_zero_candidate_artifact(tmp_path: Path) -> None:
    path = tmp_path / "stale-empty-candidates.json"
    payload = _causal_payload()
    payload["signal_date"] = "2026-01-20"
    payload["summary"] = {"candidate_count": 0, "published_count": 0}
    payload["candidates"] = []
    _write_payload(path, payload)
    reporter = preopen.Reporter(quiet=True)

    preopen.check_swing_paper_candidates(reporter, _args(path))

    assert reporter.counts["NG"] >= 1


def test_preopen_rejects_nonempty_published_rows_with_zero_summary(tmp_path: Path) -> None:
    path = tmp_path / "published-row-candidates.json"
    payload = _causal_payload()
    payload["published"] = [{"message_id": "unexpected"}]
    _write_payload(path, payload)
    reporter = preopen.Reporter(quiet=True)

    preopen.check_swing_paper_candidates(reporter, _args(path))

    assert reporter.counts["NG"] >= 1


def test_preopen_rejects_stale_reference_after_signal_close(tmp_path: Path) -> None:
    path = tmp_path / "stale-reference-candidates.json"
    payload = _causal_payload()
    candidate = payload["candidates"][0]
    candidate["valuation_reference_bar_date"] = "2026-01-20"
    candidate["valuation_reference_available_at"] = "2026-01-20T06:30:00+00:00"
    candidate["feature_data_complete"] = False
    payload["summary"]["missing_signal_date_ohlcv_count"] = 1
    _write_payload(path, payload)
    reporter = preopen.Reporter(quiet=True)

    preopen.check_swing_paper_candidates(reporter, _args(path))

    assert reporter.counts["NG"] >= 1


def test_preopen_rejects_early_zero_candidate_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "early-empty-candidates.json"
    payload = _causal_payload()
    payload["fetched_at"] = "2026-01-21T07:00:00+00:00"
    payload["summary"] = {"candidate_count": 0, "published_count": 0}
    payload["candidates"] = []
    _write_payload(path, payload)
    reporter = preopen.Reporter(quiet=True)

    preopen.check_swing_paper_candidates(reporter, _args(path))

    assert reporter.counts["NG"] >= 1


def test_preopen_rejects_late_zero_candidate_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "late-empty-candidates.json"
    payload = _causal_payload()
    payload["fetched_at"] = "2026-01-22T01:00:00+00:00"
    payload["summary"] = {"candidate_count": 0, "published_count": 0}
    payload["candidates"] = []
    _write_payload(path, payload)
    reporter = preopen.Reporter(quiet=True)

    preopen.check_swing_paper_candidates(reporter, _args(path))

    assert reporter.counts["NG"] >= 1


def test_preopen_rejects_malformed_candidate_container(tmp_path: Path) -> None:
    path = tmp_path / "malformed-candidates.json"
    payload = _causal_payload()
    payload["summary"] = {"candidate_count": 0, "published_count": 0}
    payload["candidates"] = {}
    _write_payload(path, payload)
    reporter = preopen.Reporter(quiet=True)

    preopen.check_swing_paper_candidates(reporter, _args(path))

    assert reporter.counts["NG"] >= 1


def test_preopen_rejects_missing_required_summary_counts(tmp_path: Path) -> None:
    path = tmp_path / "missing-summary-counts.json"
    payload = _causal_payload()
    payload["summary"] = {}
    payload["candidates"] = []
    _write_payload(path, payload)
    reporter = preopen.Reporter(quiet=True)

    preopen.check_swing_paper_candidates(reporter, _args(path))

    assert reporter.counts["NG"] >= 1
