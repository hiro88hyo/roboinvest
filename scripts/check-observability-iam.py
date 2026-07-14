#!/usr/bin/env python3
"""Check permissions required to manage roboinvest observability resources."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import urllib.request
from collections.abc import Sequence

REQUIRED_PERMISSIONS = (
    "logging.logMetrics.create",
    "logging.logMetrics.get",
    "logging.logMetrics.list",
    "logging.logMetrics.update",
    "monitoring.alertPolicies.create",
    "monitoring.alertPolicies.get",
    "monitoring.alertPolicies.list",
    "monitoring.alertPolicies.update",
    "monitoring.dashboards.create",
    "monitoring.dashboards.get",
    "monitoring.dashboards.update",
)


def granted_permissions(project_id: str, access_token: str) -> set[str]:
    url = f"https://cloudresourcemanager.googleapis.com/v1/projects/{project_id}:testIamPermissions"
    request = urllib.request.Request(
        url,
        data=json.dumps({"permissions": REQUIRED_PERMISSIONS}).encode(),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        document = json.load(response)
    permissions = document.get("permissions", [])
    if not isinstance(permissions, list):
        raise RuntimeError("testIamPermissions returned an invalid response")
    return {value for value in permissions if isinstance(value, str)}


def access_token() -> str:
    if shutil.which("gcloud") is None:
        raise SystemExit("gcloud is required")
    result = subprocess.run(
        ["gcloud", "auth", "print-access-token"],
        check=True,
        capture_output=True,
        text=True,
    )
    token = result.stdout.strip()
    if not token:
        raise RuntimeError("gcloud returned an empty access token")
    return token


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="Google Cloud project ID")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    granted = granted_permissions(args.project, access_token())
    missing = sorted(set(REQUIRED_PERMISSIONS) - granted)
    if missing:
        print("missing observability management permissions:")
        for permission in missing:
            print(f"- {permission}")
        return 1
    print(f"observability IAM preflight: OK ({len(granted)} permissions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
