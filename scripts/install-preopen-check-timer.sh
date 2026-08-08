#!/usr/bin/env bash
set -euo pipefail

TASK_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TASK_SYSTEMD_DIR="$(systemd-path user-configuration)/systemd/user"

mkdir -p "$TASK_SYSTEMD_DIR"
install -m 0644 "$TASK_REPO_ROOT/infra/systemd/roboinvest-preopen-check.service" \
  "$TASK_SYSTEMD_DIR/roboinvest-preopen-check.service"
install -m 0644 "$TASK_REPO_ROOT/infra/systemd/roboinvest-preopen-check.timer" \
  "$TASK_SYSTEMD_DIR/roboinvest-preopen-check.timer"

systemctl --user daemon-reload
systemctl --user enable --now roboinvest-preopen-check.timer
systemctl --user list-timers roboinvest-preopen-check.timer --all --no-pager
