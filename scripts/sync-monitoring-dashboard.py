#!/usr/bin/env python3
"""Validate and synchronize the roboinvest Cloud Monitoring dashboard."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

DEFAULT_CONFIG = Path("infra/monitoring/operations-dashboard.json")
DASHBOARD_ID = "roboinvest-operations"


def render_dashboard(path: Path, project_id: str) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8").replace("${PROJECT_ID}", project_id)
    if "${PROJECT_ID}" in text:
        raise ValueError("dashboard contains an unresolved project placeholder")
    dashboard: Any = json.loads(text)
    if not isinstance(dashboard, dict):
        raise ValueError("dashboard must be a JSON object")
    expected_name = f"projects/{project_id}/dashboards/{DASHBOARD_ID}"
    if dashboard.get("name") != expected_name:
        raise ValueError(f"dashboard name must be {expected_name}")
    if dashboard.get("labels", {}).get("managed_by") != "roboinvest":
        raise ValueError("dashboard must have managed_by=roboinvest")
    tiles = dashboard.get("mosaicLayout", {}).get("tiles")
    if not isinstance(tiles, list) or not tiles:
        raise ValueError("dashboard must contain mosaic tiles")
    return dashboard


def dashboard_exists(project_id: str) -> bool:
    result = subprocess.run(
        [
            "gcloud",
            "monitoring",
            "dashboards",
            "describe",
            DASHBOARD_ID,
            f"--project={project_id}",
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(f"dashboard describe failed (exit {result.returncode})")
    return result.returncode == 0


def sync_command(path: Path, project_id: str, *, exists: bool) -> list[str]:
    command = ["gcloud", "monitoring", "dashboards"]
    if exists:
        command.extend(["update", DASHBOARD_ID])
    else:
        command.append("create")
    command.extend([f"--project={project_id}", f"--config-from-file={path}"])
    return command


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="Google Cloud project ID")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--apply", action="store_true", help="Create or update the dashboard")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    dashboard = render_dashboard(args.config, args.project)
    if not args.apply:
        print(json.dumps(dashboard, indent=2, ensure_ascii=False))
        print("dry-run: dashboard validated; no changes made")
        return 0
    if shutil.which("gcloud") is None:
        raise SystemExit("gcloud is required with --apply")
    exists = dashboard_exists(args.project)
    with tempfile.TemporaryDirectory(prefix="roboinvest-dashboard-") as directory:
        path = Path(directory) / "dashboard.json"
        path.write_text(json.dumps(dashboard), encoding="utf-8")
        subprocess.run(sync_command(path, args.project, exists=exists), check=True)
    print(f"{'updated' if exists else 'created'}: {DASHBOARD_ID}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
