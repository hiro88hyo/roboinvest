from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "sync-alert-policies.py"
SPEC = importlib.util.spec_from_file_location("sync_alert_policies", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def valid_policy(**overrides: object) -> dict[str, object]:
    policy: dict[str, object] = {
        "id": "unexpected-errors",
        "display_name": "Errors",
        "metric": "roboinvest_unexpected_error_count",
        "severity": "ERROR",
        "threshold": 0,
        "alignment_period": "600s",
        "duration": "0s",
        "documentation": "Investigate.",
    }
    policy.update(overrides)
    return policy


def write_config(tmp_path: Path, policies: list[dict[str, object]]) -> Path:
    path = tmp_path / "policies.json"
    path.write_text(json.dumps({"schema_version": 1, "policies": policies}), encoding="utf-8")
    return path


def test_render_policy_is_disabled_and_has_stable_identity() -> None:
    rendered = module.render_policy(valid_policy(), "trade-ai-prod")

    assert rendered["enabled"] is False
    assert rendered["notificationChannels"] == []
    assert rendered["userLabels"]["roboinvest_policy_id"] == "unexpected-errors"
    condition = rendered["conditions"][0]["conditionThreshold"]
    assert "logging.googleapis.com/user/roboinvest_unexpected_error_count" in condition["filter"]
    assert condition["aggregations"][0]["alignmentPeriod"] == "600s"


def test_load_rejects_short_alignment_period(tmp_path: Path) -> None:
    path = write_config(tmp_path, [valid_policy(alignment_period="60s")])

    with pytest.raises(ValueError, match="at least 600s"):
        module.load_policy_specs(path)


def test_load_rejects_duplicate_ids(tmp_path: Path) -> None:
    path = write_config(tmp_path, [valid_policy(), valid_policy()])

    with pytest.raises(ValueError, match="duplicate policy id"):
        module.load_policy_specs(path)


def test_sync_command_selects_create_or_update(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"

    create = module.sync_command(path, "project", None)
    update = module.sync_command(path, "project", "projects/project/alertPolicies/123")

    assert create[3] == "create"
    assert update[3] == "update"
    assert "projects/project/alertPolicies/123" in update
