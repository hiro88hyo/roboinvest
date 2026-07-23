#!/usr/bin/env bash
set -euo pipefail

TASK_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TASK_SYSTEMD_DIR="$(systemd-path user-configuration)/systemd/user"

mkdir -p "$TASK_SYSTEMD_DIR"
install -m 0644 "$TASK_REPO_ROOT/infra/systemd/roboinvest-shadow-forward-evidence.service" \
  "$TASK_SYSTEMD_DIR/roboinvest-shadow-forward-evidence.service"
install -m 0644 "$TASK_REPO_ROOT/infra/systemd/roboinvest-shadow-forward-evidence.timer" \
  "$TASK_SYSTEMD_DIR/roboinvest-shadow-forward-evidence.timer"

systemctl --user daemon-reload
systemctl --user enable --now roboinvest-shadow-forward-evidence.timer
systemctl --user list-timers roboinvest-shadow-forward-evidence.timer --all
