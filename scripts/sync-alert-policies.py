#!/usr/bin/env python3
"""Validate and synchronize disabled Cloud Monitoring alert policies."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

DEFAULT_CONFIG = Path("infra/monitoring/alert-policies.json")
POLICY_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
DURATION_RE = re.compile(r"^(0|[1-9][0-9]*)s$")
SEVERITIES = {"WARNING", "ERROR", "CRITICAL"}


def load_policy_specs(path: Path) -> list[dict[str, Any]]:
    document: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ValueError("config must be an object with schema_version=1")
    rows = document.get("policies")
    if not isinstance(rows, list) or not rows:
        raise ValueError("config must contain a non-empty policies list")

    seen: set[str] = set()
    required = {
        "id",
        "display_name",
        "metric",
        "severity",
        "threshold",
        "alignment_period",
        "duration",
        "documentation",
    }
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != required:
            raise ValueError(f"policies[{index}] must contain exactly {sorted(required)}")
        policy_id = row["id"]
        if not isinstance(policy_id, str) or not POLICY_ID_RE.fullmatch(policy_id):
            raise ValueError(f"policies[{index}].id is invalid: {policy_id!r}")
        if policy_id in seen:
            raise ValueError(f"duplicate policy id: {policy_id}")
        seen.add(policy_id)
        for field in ("display_name", "metric", "documentation"):
            if not isinstance(row[field], str) or not row[field].strip():
                raise ValueError(f"policies[{index}].{field} must be non-empty")
        if row["severity"] not in SEVERITIES:
            raise ValueError(f"policies[{index}].severity is invalid")
        if not isinstance(row["threshold"], (int, float)):
            raise ValueError(f"policies[{index}].threshold must be numeric")
        for field in ("alignment_period", "duration"):
            value = row[field]
            if not isinstance(value, str) or not DURATION_RE.fullmatch(value):
                raise ValueError(f"policies[{index}].{field} is invalid")
        alignment_seconds = int(row["alignment_period"][:-1])
        if alignment_seconds < 600:
            raise ValueError(f"policies[{index}].alignment_period must be at least 600s")
    return rows


def render_policy(spec: dict[str, Any], project_id: str) -> dict[str, Any]:
    metric_type = f"logging.googleapis.com/user/{spec['metric']}"
    return {
        "displayName": spec["display_name"],
        "combiner": "OR",
        "enabled": False,
        "severity": spec["severity"],
        "userLabels": {
            "managed_by": "roboinvest",
            "roboinvest_policy_id": spec["id"],
        },
        "documentation": {
            "content": spec["documentation"],
            "mimeType": "text/markdown",
        },
        "conditions": [
            {
                "displayName": f"{spec['metric']} > {spec['threshold']}",
                "conditionThreshold": {
                    "filter": (f'metric.type="{metric_type}" AND resource.type="global"'),
                    "comparison": "COMPARISON_GT",
                    "thresholdValue": spec["threshold"],
                    "duration": spec["duration"],
                    "trigger": {"count": 1},
                    "aggregations": [
                        {
                            "alignmentPeriod": spec["alignment_period"],
                            "perSeriesAligner": "ALIGN_SUM",
                            "crossSeriesReducer": "REDUCE_SUM",
                        }
                    ],
                },
            }
        ],
        "notificationChannels": [],
    }


def list_managed_policies(project_id: str) -> dict[str, str]:
    result = subprocess.run(
        [
            "gcloud",
            "monitoring",
            "policies",
            "list",
            f"--project={project_id}",
            "--format=json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    policies = json.loads(result.stdout)
    managed: dict[str, str] = {}
    for policy in policies:
        labels = policy.get("userLabels", {})
        if labels.get("managed_by") != "roboinvest":
            continue
        policy_id = labels.get("roboinvest_policy_id")
        name = policy.get("name")
        if policy_id and name:
            if policy_id in managed:
                raise RuntimeError(f"duplicate managed policy in Cloud: {policy_id}")
            managed[policy_id] = name
    return managed


def sync_command(path: Path, project_id: str, existing_name: str | None) -> list[str]:
    if existing_name:
        return [
            "gcloud",
            "monitoring",
            "policies",
            "update",
            existing_name,
            f"--project={project_id}",
            f"--policy-from-file={path}",
        ]
    return [
        "gcloud",
        "monitoring",
        "policies",
        "create",
        f"--project={project_id}",
        f"--policy-from-file={path}",
    ]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="Google Cloud project ID")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Create or replace disabled policies. Default is validation only.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    specs = load_policy_specs(args.config)
    rendered = [(spec["id"], render_policy(spec, args.project)) for spec in specs]
    if not args.apply:
        print(json.dumps(dict(rendered), indent=2, ensure_ascii=False))
        print(f"dry-run: validated {len(rendered)} disabled policies; no changes made")
        return 0

    if shutil.which("gcloud") is None:
        raise SystemExit("gcloud is required with --apply")
    existing = list_managed_policies(args.project)
    with tempfile.TemporaryDirectory(prefix="roboinvest-alerts-") as directory:
        for policy_id, policy in rendered:
            path = Path(directory) / f"{policy_id}.json"
            path.write_text(json.dumps(policy), encoding="utf-8")
            command = sync_command(path, args.project, existing.get(policy_id))
            subprocess.run(command, check=True)
            print(f"{'updated' if policy_id in existing else 'created'}: {policy_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
