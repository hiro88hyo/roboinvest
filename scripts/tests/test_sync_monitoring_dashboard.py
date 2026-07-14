from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "sync-monitoring-dashboard.py"
SPEC = importlib.util.spec_from_file_location("sync_monitoring_dashboard", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def test_render_dashboard_substitutes_project_and_has_operational_widgets() -> None:
    path = Path("infra/monitoring/operations-dashboard.json")

    dashboard = module.render_dashboard(path, "trade-ai-prod")

    assert dashboard["name"] == ("projects/trade-ai-prod/dashboards/roboinvest-operations")
    assert dashboard["labels"]["managed_by"] == "roboinvest"
    serialized = json.dumps(dashboard)
    assert "${PROJECT_ID}" not in serialized
    assert "oldest_unacked_message_age" in serialized
    assert "logsPanel" in serialized


def test_sync_command_selects_create_or_update(tmp_path: Path) -> None:
    path = tmp_path / "dashboard.json"

    create = module.sync_command(path, "project", exists=False)
    update = module.sync_command(path, "project", exists=True)

    assert create[3] == "create"
    assert update[3:5] == ["update", "roboinvest-operations"]
