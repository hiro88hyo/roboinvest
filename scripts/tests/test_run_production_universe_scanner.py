from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

SOURCE_SCRIPT = Path(__file__).resolve().parents[1] / "run-production-universe-scanner.sh"
SERVICE_UNIT = (
    Path(__file__).resolve().parents[2] / "infra/systemd/roboinvest-universe-scanner.service"
)


def test_empty_credentials_directory_is_repaired_at_stable_path_and_reused(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    scripts_dir = repo / "scripts"
    infra_dir = repo / "infra"
    fake_bin = tmp_path / "bin"
    scripts_dir.mkdir(parents=True)
    infra_dir.mkdir()
    fake_bin.mkdir()

    wrapper = scripts_dir / SOURCE_SCRIPT.name
    shutil.copy2(SOURCE_SCRIPT, wrapper)
    (infra_dir / "env.production").write_text("TEST_ONLY=1\n", encoding="utf-8")

    fake_op = fake_bin / "op"
    fake_op.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [ "${1:-}" = "read" ]; then
  output=""
  shift
  while [ "$#" -gt 0 ]; do
    if [ "$1" = "--out-file" ]; then
      output="$2"
      shift 2
    else
      shift
    fi
  done
  printf '{"type":"service_account"}\\n' > "$output"
  count=0
  if [ -f "$FAKE_OP_READ_COUNT_FILE" ]; then
    count="$(<"$FAKE_OP_READ_COUNT_FILE")"
  fi
  printf '%d\\n' "$((count + 1))" > "$FAKE_OP_READ_COUNT_FILE"
fi
""",
        encoding="utf-8",
    )
    fake_op.chmod(0o755)

    credentials = tmp_path / "stable" / "gcp.json"
    credentials.mkdir(parents=True)
    read_count = tmp_path / "op-read-count"
    lock_dir = tmp_path / "scanner.lock"
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "OP_SERVICE_ACCOUNT_TOKEN": "test-token",
        "GOOGLE_APPLICATION_CREDENTIALS_HOST_PATH": str(credentials),
        "ROBOINVEST_UNIVERSE_SCANNER_LOCK_DIR": str(lock_dir),
        "FAKE_OP_READ_COUNT_FILE": str(read_count),
    }
    command = [
        "bash",
        str(wrapper),
        "--date",
        "2026-09-15",
        "--skip-health-check",
        "--skip-oms-live-sync",
    ]

    first = subprocess.run(command, cwd=repo, env=env, text=True, capture_output=True)
    second = subprocess.run(command, cwd=repo, env=env, text=True, capture_output=True)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert "materialized persistent Pub/Sub credentials" in first.stdout
    assert "materialized persistent Pub/Sub credentials" not in second.stdout
    assert json.loads(credentials.read_text(encoding="utf-8"))["type"] == "service_account"
    assert stat.S_IMODE(credentials.stat().st_mode) == 0o600
    assert read_count.read_text(encoding="utf-8").strip() == "1"
    assert not list(credentials.parent.glob("gcp.json.tmp.*"))


def test_systemd_service_retries_bounded_failures() -> None:
    unit = SERVICE_UNIT.read_text(encoding="utf-8")

    assert "Restart=on-failure" in unit
    assert "RestartSec=60s" in unit
    assert "StartLimitIntervalSec=15min" in unit
    assert "StartLimitBurst=3" in unit
