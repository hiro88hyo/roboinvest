from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).parents[1] / "run-event-forward-evidence.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_event_forward_evidence", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_commands_keep_explicit_date_and_causal_output_identity() -> None:
    module = _load_script()
    commands = module.commands(date(2026, 7, 13))

    assert len(commands) == 4
    assert all(command[0] == sys.executable for command in commands)
    assert commands[0][commands[0].index("--resume")] == "--resume"
    assert any(value.endswith("causal-candidates-2026-07-13.json") for value in commands[2])
    assert commands[3][-1] == "out/event-forward-evidence/ledger.jsonl"


@pytest.mark.parametrize("value", [date(2026, 6, 30), date(2026, 7, 12)])
def test_preflight_rejects_pre_forward_or_non_business_date(
    value: date, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_script()
    monkeypatch.setenv("JQUANTS_API_KEY", "fixture")

    with pytest.raises(ValueError):
        module.preflight(value)


def test_preflight_refuses_overwrite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_script()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JQUANTS_API_KEY", "fixture")
    output_json, _output_csv = module.output_paths(date(2026, 7, 13))
    output_json.parent.mkdir(parents=True)
    output_json.write_text("existing", encoding="utf-8")

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        module.preflight(date(2026, 7, 13))


def test_preflight_requires_secret_injection(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_script()
    monkeypatch.delenv("JQUANTS_API_KEY", raising=False)
    monkeypatch.delenv("JQUANTS_MAIL_ADDRESS", raising=False)
    monkeypatch.delenv("JQUANTS_PASSWORD", raising=False)

    with pytest.raises(ValueError, match="op run"):
        module.preflight(date(2026, 7, 13))
