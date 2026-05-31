#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "google-cloud-pubsub>=2.38",
#   "httpx>=0.27",
# ]
# ///
"""Production pre-open checks for the roboinvest stack.

Run this under resolved production env, for example:

    set -a && . infra/.op.service-account.env && set +a
    op run --env-file infra/env.production -- \\
      uv run python scripts/production-preopen-check.py --kabu-offline

Use ``--kabu-offline`` outside market/pre-open hours when kabu station or the
Windows proxy is intentionally stopped. In that mode feeder restart / kabu 502
is reported as WARN instead of failing the whole check.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from google.api_core.exceptions import GoogleAPICallError, NotFound
from google.cloud import pubsub_v1

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILE = REPO_ROOT / "infra" / "docker-compose.prod.yml"
ENV_FILE = REPO_ROOT / "infra" / "env.production"
TOPICS_JSON = REPO_ROOT / "infra" / "pubsub" / "topics.json"
SUBSCRIPTIONS_JSON = REPO_ROOT / "infra" / "pubsub" / "subscriptions.json"
DEFAULT_HOST_GCP_CREDENTIALS = Path("/dev/shm/roboinvest/gcp-pubsub-sa.json")
SMOKE_TOPIC = "adr-0001-smoke-test"
SMOKE_SUBSCRIPTION = "adr-0001-smoke-test-sub"

SUPABASE_TABLES = (
    "system_status",
    "positions",
    "strategy_logs",
    "aggregator_logs",
    "trades_live",
    "trades_paper",
    "watchlist",
    "master_stocks",
    "daily_ohlcv",
)

TARGET_SERVICES = ("strategy-ai", "aggregator", "gateway")
CORE_SERVICES = (
    "strategy-ai",
    "aggregator",
    "gateway",
    "feature-engine",
    "strategy-rule",
    "oms-live",
)

EXPECTED_ENV = {
    "AI_MAX_OUTPUT_TOKENS": "2048",
    "LIVE_DAY_NEW_BUY_START_TIME": "09:15",
    "MIN_CONFIDENCE_RULE_ONLY": "0.5",
    "MIN_CONFIDENCE_AI_ONLY": "0.5",
    "MIN_CONFIDENCE_CONSENSUS": "0.3",
}


@dataclass(slots=True)
class Reporter:
    quiet: bool = False
    counts: dict[str, int] = field(default_factory=lambda: {"OK": 0, "WARN": 0, "NG": 0, "SKIP": 0})

    def section(self, label: str) -> None:
        print(f"\n=== {label} ===", flush=True)

    def emit(self, status: str, label: str, detail: str = "") -> None:
        self.counts[status] += 1
        if self.quiet and status == "OK":
            return
        suffix = f" ({detail})" if detail else ""
        print(f"  {status:<4} {label}{suffix}", flush=True)

    @property
    def failed(self) -> bool:
        return self.counts["NG"] > 0

    def summary(self) -> None:
        self.section("summary")
        for status in ("OK", "WARN", "NG", "SKIP"):
            print(f"  {status:<4} {self.counts[status]}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ENV_FILE)
    parser.add_argument("--compose-file", type=Path, default=COMPOSE_FILE)
    parser.add_argument(
        "--gcp-credentials",
        type=Path,
        default=DEFAULT_HOST_GCP_CREDENTIALS,
        help="Host path to the GCP service account JSON for managed Pub/Sub checks.",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--no-pubsub-smoke", action="store_true")
    parser.add_argument(
        "--kabu-offline",
        action="store_true",
        help=(
            "Treat feeder/kabu connectivity failures as WARN because "
            "kabu station is intentionally stopped."
        ),
    )
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


def _run(cmd: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _compose_cmd(args: argparse.Namespace, *extra: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--env-file",
        str(args.env_file),
        "-f",
        str(args.compose_file),
        *extra,
    ]


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _topic_path(project_id: str, topic: str) -> str:
    return pubsub_v1.PublisherClient.topic_path(project_id, topic)


def _subscription_path(project_id: str, subscription: str) -> str:
    return pubsub_v1.SubscriberClient.subscription_path(project_id, subscription)


def check_expected_env(reporter: Reporter) -> None:
    reporter.section("production env")
    for key, expected in EXPECTED_ENV.items():
        actual = os.environ.get(key, "")
        if actual == expected:
            reporter.emit("OK", key, actual)
        elif actual:
            reporter.emit("NG", key, f"actual={actual} expected={expected}")
        else:
            reporter.emit("NG", key, f"missing expected={expected}")

    for key in ("TRADE_MODE", "OMS_LIVE_DRY_RUN", "GEMINI_MODEL", "OMS_LIVE_MAX_QTY_PER_ORDER"):
        value = os.environ.get(key, "")
        reporter.emit("OK" if value else "WARN", key, value or "missing")


def check_compose(reporter: Reporter, args: argparse.Namespace) -> None:
    reporter.section("docker compose")
    proc = _run(_compose_cmd(args, "ps", "--format", "json"), timeout=args.timeout)
    if proc.returncode != 0:
        reporter.emit("NG", "compose ps", (proc.stderr or proc.stdout).strip()[:240])
        return

    rows: list[dict[str, Any]] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            reporter.emit("NG", "compose ps parse", line[:240])
            return

    by_service = {str(row.get("Service")): row for row in rows}
    for service in CORE_SERVICES:
        row = by_service.get(service)
        if not row:
            reporter.emit("NG", service, "container missing")
            continue
        state = str(row.get("State") or "")
        status = str(row.get("Status") or "")
        if state == "running":
            reporter.emit("OK", service, status)
        else:
            reporter.emit("NG", service, f"state={state} status={status}")

    feeder = by_service.get("feeder")
    if feeder is None:
        reporter.emit("WARN" if args.kabu_offline else "NG", "feeder", "container missing")
    else:
        state = str(feeder.get("State") or "")
        status = str(feeder.get("Status") or "")
        if state == "running":
            reporter.emit("OK", "feeder", status)
        elif args.kabu_offline:
            reporter.emit("WARN", "feeder", f"{status}; kabu-offline accepted")
        else:
            reporter.emit("NG", "feeder", f"state={state} status={status}")


def check_container_env(reporter: Reporter, args: argparse.Namespace) -> None:
    reporter.section("container env")
    probes = {
        "strategy-ai": ("AI_MAX_OUTPUT_TOKENS", "GEMINI_MODEL"),
        "gateway": ("LIVE_DAY_NEW_BUY_START_TIME", "LIVE_DAY_NEW_BUY_CUTOFF_TIME", "TRADE_MODE"),
        "aggregator": (
            "MIN_CONFIDENCE_RULE_ONLY",
            "MIN_CONFIDENCE_AI_ONLY",
            "MIN_CONFIDENCE_CONSENSUS",
            "CONFLICT_POLICY",
        ),
    }
    for service, keys in probes.items():
        script = " && ".join([f'printf "{key}=%s\\n" "${key}"' for key in keys])
        proc = _run(
            _compose_cmd(args, "exec", "-T", service, "sh", "-c", script),
            timeout=args.timeout,
        )
        if proc.returncode != 0:
            reporter.emit("NG", f"{service}:env", (proc.stderr or proc.stdout).strip()[:240])
            continue
        seen: dict[str, str] = {}
        for line in proc.stdout.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                seen[key] = value
        for key in keys:
            expected = EXPECTED_ENV.get(key)
            value = seen.get(key, "")
            if expected is None:
                reporter.emit("OK" if value else "WARN", f"{service}:{key}", value or "missing")
            elif value == expected:
                reporter.emit("OK", f"{service}:{key}", value)
            else:
                reporter.emit(
                    "NG",
                    f"{service}:{key}",
                    f"actual={value or '<missing>'} expected={expected}",
                )


def check_supabase(reporter: Reporter, args: argparse.Namespace) -> None:
    reporter.section("Supabase")
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SECRET_KEY", "")
    if not url or not key:
        reporter.emit("NG", "env", "SUPABASE_URL / SUPABASE_SECRET_KEY missing")
        return
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    with httpx.Client(base_url=url, headers=headers, timeout=args.timeout) as client:
        for table in SUPABASE_TABLES:
            resp = client.get(f"/rest/v1/{table}", params={"select": "*", "limit": 0})
            if resp.status_code == 200:
                reporter.emit("OK", f"table:{table}")
            else:
                reporter.emit("NG", f"table:{table}", f"HTTP {resp.status_code} {resp.text[:120]}")

        resp = client.get(
            "/rest/v1/system_status",
            params={
                "select": (
                    "id,is_trading_allowed,trade_mode,trading_style,"
                    "daily_pnl,daily_loss_limit,updated_at"
                ),
                "id": "eq.1",
            },
        )
        if resp.status_code != 200:
            reporter.emit("NG", "system_status", f"HTTP {resp.status_code} {resp.text[:120]}")
        else:
            rows = resp.json()
            row = rows[0] if rows else None
            if not row:
                reporter.emit("NG", "system_status", "id=1 missing")
            else:
                allowed = row.get("is_trading_allowed") is True
                trade_mode = row.get("trade_mode")
                trading_style = row.get("trading_style")
                daily_pnl = Decimal(str(row.get("daily_pnl", "0")))
                daily_loss_limit = Decimal(str(row.get("daily_loss_limit", "0")))
                reporter.emit("OK" if allowed else "NG", "is_trading_allowed", str(allowed).lower())
                reporter.emit(
                    "OK" if trade_mode == "live" else "WARN",
                    "trade_mode",
                    str(trade_mode),
                )
                reporter.emit(
                    "OK" if trading_style == "day" else "WARN",
                    "trading_style",
                    str(trading_style),
                )
                if daily_loss_limit > 0 and daily_pnl <= -daily_loss_limit:
                    reporter.emit("NG", "daily_pnl", f"{daily_pnl} <= -{daily_loss_limit}")
                else:
                    reporter.emit("OK", "daily_pnl", f"{daily_pnl} limit={daily_loss_limit}")

        resp = client.get(
            "/rest/v1/positions",
            params={
                "select": "symbol,quantity,trade_type,side,opened_at",
                "trade_type": "eq.live",
                "order": "symbol.asc",
            },
        )
        if resp.status_code == 200:
            positions = resp.json()
            if positions:
                symbols = ", ".join(f"{p.get('symbol')}x{p.get('quantity')}" for p in positions)
                reporter.emit("WARN", "live positions", symbols)
            else:
                reporter.emit("OK", "live positions", "empty")
        else:
            reporter.emit("NG", "live positions", f"HTTP {resp.status_code} {resp.text[:120]}")


def _load_topics() -> list[str]:
    payload = _load_json(TOPICS_JSON)
    return [str(topic) for topic in payload["topics"]]


def _load_subscriptions() -> list[tuple[str, str]]:
    payload = _load_json(SUBSCRIPTIONS_JSON)
    return [(str(item["name"]), str(item["topic"])) for item in payload["subscriptions"]]


def check_pubsub(reporter: Reporter, args: argparse.Namespace) -> None:
    reporter.section("managed Pub/Sub")
    project_id = os.environ.get("PUBSUB_PROJECT_ID", "")
    if not project_id:
        reporter.emit("NG", "PUBSUB_PROJECT_ID", "missing")
        return
    credentials = args.gcp_credentials
    if not credentials.exists():
        reporter.emit("NG", "GOOGLE_APPLICATION_CREDENTIALS", f"missing host file: {credentials}")
        return
    old_credentials = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(credentials)
    try:
        publisher = pubsub_v1.PublisherClient()
        subscriber = pubsub_v1.SubscriberClient()
        try:
            for topic in _load_topics():
                try:
                    publisher.get_topic(
                        request={"topic": _topic_path(project_id, topic)},
                        timeout=args.timeout,
                    )
                    reporter.emit("OK", f"topic:{topic}")
                except NotFound:
                    reporter.emit("NG", f"topic:{topic}", "missing")
                except GoogleAPICallError as exc:
                    reporter.emit("NG", f"topic:{topic}", repr(exc)[:160])

            for name, topic in _load_subscriptions():
                try:
                    sub = subscriber.get_subscription(
                        request={"subscription": _subscription_path(project_id, name)},
                        timeout=args.timeout,
                    )
                    actual = sub.topic.rsplit("/", 1)[-1]
                    if actual == topic:
                        reporter.emit("OK", f"sub:{name}", f"-> {topic}")
                    else:
                        reporter.emit("NG", f"sub:{name}", f"actual={actual} expected={topic}")
                except NotFound:
                    reporter.emit("NG", f"sub:{name}", "missing")
                except GoogleAPICallError as exc:
                    reporter.emit("NG", f"sub:{name}", repr(exc)[:160])

            if not args.no_pubsub_smoke:
                _pubsub_smoke(reporter, project_id, publisher, subscriber, args.timeout)
        finally:
            publisher.transport.close()
            subscriber.close()
    finally:
        if old_credentials is None:
            os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
        else:
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = old_credentials


def _pubsub_smoke(
    reporter: Reporter,
    project_id: str,
    publisher: pubsub_v1.PublisherClient,
    subscriber: pubsub_v1.SubscriberClient,
    timeout: float,
) -> None:
    topic = _topic_path(project_id, SMOKE_TOPIC)
    subscription = _subscription_path(project_id, SMOKE_SUBSCRIPTION)
    try:
        publisher.get_topic(request={"topic": topic}, timeout=timeout)
        subscriber.get_subscription(request={"subscription": subscription}, timeout=timeout)
        message_id = publisher.publish(
            topic,
            f"preopen-check {int(time.time())}".encode(),
            purpose="production-preopen-check",
        ).result(timeout=timeout)
        response = subscriber.pull(
            request={"subscription": subscription, "max_messages": 1},
            timeout=timeout,
        )
        if not response.received_messages:
            reporter.emit("NG", "smoke", f"publish={message_id} pull=empty")
            return
        ack_ids = [msg.ack_id for msg in response.received_messages]
        subscriber.acknowledge(
            request={"subscription": subscription, "ack_ids": ack_ids},
            timeout=timeout,
        )
        reporter.emit("OK", "smoke", f"publish/pull/ack message_id={message_id}")
    except Exception as exc:
        reporter.emit("NG", "smoke", repr(exc)[:200])


def check_feeder_logs(reporter: Reporter, args: argparse.Namespace) -> None:
    reporter.section("feeder kabu logs")
    proc = _run(_compose_cmd(args, "logs", "--tail", "80", "feeder"), timeout=args.timeout)
    if proc.returncode != 0:
        reporter.emit(
            "WARN" if args.kabu_offline else "NG",
            "feeder logs",
            (proc.stderr or proc.stdout)[:200],
        )
        return
    text = proc.stdout + proc.stderr
    if "HTTP 502" in text or "Bad Gateway" in text:
        reporter.emit("WARN" if args.kabu_offline else "NG", "kabu websocket", "HTTP 502")
    elif "Traceback" in text:
        reporter.emit("WARN" if args.kabu_offline else "NG", "feeder logs", "traceback present")
    else:
        reporter.emit("OK", "feeder logs", "no recent kabu error")


def main() -> int:
    args = parse_args()
    reporter = Reporter(quiet=args.quiet)

    check_expected_env(reporter)
    check_compose(reporter, args)
    check_container_env(reporter, args)
    check_supabase(reporter, args)
    check_pubsub(reporter, args)
    check_feeder_logs(reporter, args)
    reporter.summary()
    return 1 if reporter.failed else 0


if __name__ == "__main__":
    sys.exit(main())
