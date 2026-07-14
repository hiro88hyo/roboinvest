#!/usr/bin/env python3
"""Validate and synchronize Cloud Logging counter metrics.

Dry-run is the default. Applying changes requires an explicit ``--apply``.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_CONFIG = Path("infra/monitoring/log-based-metrics.json")
METRIC_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,99}$")


@dataclass(frozen=True)
class LogMetric:
    name: str
    description: str
    filter: str


def load_metrics(path: Path, project_id: str) -> list[LogMetric]:
    document: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ValueError("config must be an object with schema_version=1")
    rows = document.get("metrics")
    if not isinstance(rows, list) or not rows:
        raise ValueError("config must contain a non-empty metrics list")

    metrics: list[LogMetric] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"metrics[{index}] must be an object")
        name = row.get("name")
        description = row.get("description")
        filter_template = row.get("filter")
        if not isinstance(name, str) or not METRIC_NAME_RE.fullmatch(name):
            raise ValueError(f"metrics[{index}].name is invalid: {name!r}")
        if name in seen:
            raise ValueError(f"duplicate metric name: {name}")
        if not isinstance(description, str) or not description.strip():
            raise ValueError(f"metrics[{index}].description must be non-empty")
        if not isinstance(filter_template, str) or not filter_template.strip():
            raise ValueError(f"metrics[{index}].filter must be non-empty")
        if "${PROJECT_ID}" not in filter_template:
            raise ValueError(f"metrics[{index}].filter must scope logName with ${{PROJECT_ID}}")
        filter_value = filter_template.replace("${PROJECT_ID}", project_id)
        if "${" in filter_value:
            raise ValueError(f"metrics[{index}].filter contains an unknown placeholder")
        seen.add(name)
        metrics.append(LogMetric(name, description, filter_value))
    return metrics


def gcloud_command(metric: LogMetric, project_id: str, *, exists: bool) -> list[str]:
    action = "update" if exists else "create"
    return [
        "gcloud",
        "logging",
        "metrics",
        action,
        metric.name,
        f"--project={project_id}",
        f"--description={metric.description}",
        f"--log-filter={metric.filter}",
    ]


def metric_exists(name: str, project_id: str) -> bool:
    result = subprocess.run(
        [
            "gcloud",
            "logging",
            "metrics",
            "describe",
            name,
            f"--project={project_id}",
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(f"gcloud describe failed for {name} (exit {result.returncode})")
    return result.returncode == 0


def shell_display(command: Sequence[str]) -> str:
    import shlex

    return shlex.join(command)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="Google Cloud project ID")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Create or update metrics. Without this flag, only validate and print.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    metrics = load_metrics(args.config, args.project)
    if not args.apply:
        for metric in metrics:
            print(shell_display(gcloud_command(metric, args.project, exists=False)))
        print(f"dry-run: validated {len(metrics)} metrics; no cloud changes made")
        return 0

    if shutil.which("gcloud") is None:
        raise SystemExit("gcloud is required with --apply")
    for metric in metrics:
        exists = metric_exists(metric.name, args.project)
        command = gcloud_command(metric, args.project, exists=exists)
        subprocess.run(command, check=True)
        print(f"{'updated' if exists else 'created'}: {metric.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
