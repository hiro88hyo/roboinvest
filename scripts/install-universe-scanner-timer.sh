#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SYSTEMD_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

mkdir -p "$SYSTEMD_DIR"
install -m 0644 "$REPO_ROOT/infra/systemd/roboinvest-universe-scanner.service" \
  "$SYSTEMD_DIR/roboinvest-universe-scanner.service"
install -m 0644 "$REPO_ROOT/infra/systemd/roboinvest-universe-scanner.timer" \
  "$SYSTEMD_DIR/roboinvest-universe-scanner.timer"

systemctl --user daemon-reload
systemctl --user enable --now roboinvest-universe-scanner.timer
systemctl --user list-timers roboinvest-universe-scanner.timer --all
