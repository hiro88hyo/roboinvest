#!/usr/bin/env bash
set -euo pipefail

compose=(docker compose -f infra/docker-compose.observability-test.yml)
service=otel-collector-parse-fixture

cleanup() {
  "${compose[@]}" down --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

"${compose[@]}" up -d --force-recreate "$service" >/dev/null
sleep "${OTEL_PARSE_VERIFY_SLEEP_SECONDS:-4}"

logs="$("${compose[@]}" logs --no-color "$service")"
printf '%s\n' "$logs"

printf '%s\n' "$logs" | rg -q 'event.*signal_rejected'
printf '%s\n' "$logs" | rg -q 'SeverityText: ERROR'
printf '%s\n' "$logs" | rg -q 'docker_stream: Str\(stderr\)'
if printf '%s\n' "$logs" | rg -q 'plain startup line from a non-json process'; then
  printf 'unexpected non-json log was exported\n' >&2
  exit 1
fi

printf 'otel collector parse fixture: ok\n'
