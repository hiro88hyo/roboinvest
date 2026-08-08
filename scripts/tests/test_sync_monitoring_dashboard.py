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
    assert "roboinvest_feature_received_per_window" in serialized
    assert "roboinvest_feature_latest_tick_age_seconds" in serialized
    assert "roboinvest_oms_paper_books_applied_per_window" in serialized
    assert "roboinvest_oms_paper_latest_book_age_seconds" in serialized
    assert serialized.count('"scorecard"') == 4


def test_sync_command_selects_create_or_update(tmp_path: Path) -> None:
    path = tmp_path / "dashboard.json"

    create = module.sync_command(path, "project", exists=False)
    update = module.sync_command(path, "project", exists=True)

    assert create[3] == "create"
    assert update[3:5] == ["update", "roboinvest-operations"]


def test_prepare_dashboard_update_sets_etag_only_for_existing_dashboard() -> None:
    dashboard = {"name": "projects/project/dashboards/roboinvest-operations"}

    create = module.prepare_dashboard_update(dashboard, None)
    update = module.prepare_dashboard_update(dashboard, "existing-etag")

    assert "etag" not in create
    assert update["etag"] == "existing-etag"
    assert "etag" not in dashboard
