from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "check-observability-iam.py"
SPEC = importlib.util.spec_from_file_location("check_observability_iam", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def test_main_reports_missing_permissions(monkeypatch, capsys) -> None:
    monkeypatch.setattr(module, "access_token", lambda: "token")
    monkeypatch.setattr(
        module,
        "granted_permissions",
        lambda project, token: {"logging.logMetrics.get"},
    )

    result = module.main(["--project", "project"])

    assert result == 1
    output = capsys.readouterr().out
    assert "logging.logMetrics.create" in output
    assert "monitoring.alertPolicies.create" in output


def test_main_succeeds_when_all_permissions_are_granted(monkeypatch, capsys) -> None:
    monkeypatch.setattr(module, "access_token", lambda: "token")
    monkeypatch.setattr(
        module,
        "granted_permissions",
        lambda project, token: set(module.REQUIRED_PERMISSIONS),
    )

    result = module.main(["--project", "project"])

    assert result == 0
    assert "preflight: OK" in capsys.readouterr().out
