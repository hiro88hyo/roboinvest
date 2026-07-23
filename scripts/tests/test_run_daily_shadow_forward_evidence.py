from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import date, datetime
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).parents[1] / "run-daily-shadow-forward-evidence.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_daily_shadow_forward_evidence", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_previous_jst_date_uses_calendar_day_not_utc_day() -> None:
    module = _load_script()

    assert module.previous_jst_date(datetime.fromisoformat("2026-07-24T00:10:00+09:00")) == date(
        2026, 7, 23
    )


def test_non_business_day_is_a_successful_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_script()
    monkeypatch.chdir(tmp_path)
    called = False

    def fake_run(*args: object, **kwargs: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert module.run_daily(now=datetime.fromisoformat("2026-07-20T00:10:00+09:00")) == 0
    assert called is False


def test_pending_business_day_invokes_frozen_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_script()
    monkeypatch.chdir(tmp_path)
    calls: list[tuple[list[str], bool]] = []

    def fake_run(command: list[str], *, check: bool) -> None:
        calls.append((command, check))
        artifact_json, artifact_csv = module.artifact_paths(date(2026, 7, 23))
        artifact_json.parent.mkdir(parents=True)
        artifact_json.write_text("{}", encoding="utf-8")
        artifact_csv.write_text("header\n", encoding="utf-8")
        module.LEDGER.parent.mkdir(parents=True)
        row = {
            "signal_date": "2026-07-23",
            "artifact_path": str(artifact_json),
            "artifact_sha256": hashlib.sha256(b"{}").hexdigest(),
        }
        module.LEDGER.write_text(json.dumps(row) + "\n", encoding="utf-8")

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert module.run_daily(now=datetime.fromisoformat("2026-07-24T00:10:00+09:00")) == 0
    assert calls == [
        (
            [
                sys.executable,
                "scripts/run-event-forward-evidence.py",
                "--signal-date",
                "2026-07-23",
            ],
            True,
        )
    ]


def test_completed_business_day_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_script()
    monkeypatch.chdir(tmp_path)
    artifact_json, artifact_csv = module.artifact_paths(date(2026, 7, 23))
    artifact_json.parent.mkdir(parents=True)
    artifact_json.write_text("{}", encoding="utf-8")
    artifact_csv.write_text("header\n", encoding="utf-8")
    module.LEDGER.parent.mkdir(parents=True)
    module.LEDGER.write_text(
        json.dumps(
            {
                "signal_date": "2026-07-23",
                "artifact_path": str(artifact_json),
                "artifact_sha256": hashlib.sha256(b"{}").hexdigest(),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("completed date must not rerun"),
    )

    assert module.run_daily(now=datetime.fromisoformat("2026-07-24T00:10:00+09:00")) == 0


def test_partial_artifact_state_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_script()
    monkeypatch.chdir(tmp_path)
    artifact_json, _ = module.artifact_paths(date(2026, 7, 23))
    artifact_json.parent.mkdir(parents=True)
    artifact_json.write_text("{}", encoding="utf-8")

    with pytest.raises(RuntimeError, match="inconsistent"):
        module.run_daily(now=datetime.fromisoformat("2026-07-24T00:10:00+09:00"))


def test_post_evaluation_business_day_runs_outcome_maintenance_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_script()
    monkeypatch.chdir(tmp_path)
    calls: list[list[str]] = []

    def fake_run(command: list[str], *, check: bool) -> None:
        assert check is True
        calls.append(command)

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert module.run_daily(now=datetime.fromisoformat("2026-08-29T00:10:00+09:00")) == 0
    assert len(calls) == 3
    assert calls[0][1] == "scripts/export-jquants-daily-ohlcv-csv.py"
    assert calls[1][1] == "scripts/finalize-event-forward-outcomes.py"
    assert calls[2][1] == "scripts/report-project-kill-switch-readiness.py"
    assert all("scripts/run-event-forward-evidence.py" not in call for call in calls)


def test_dates_after_project_window_are_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_script()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("post-window date must not run"),
    )

    assert module.run_daily(now=datetime.fromisoformat("2026-10-02T00:10:00+09:00")) == 0
