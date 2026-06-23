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
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from datetime import time as datetime_time
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from google.api_core.exceptions import GoogleAPICallError, NotFound
from google.cloud import pubsub_v1
from trade_contracts.scanner_gate import ScannerGateThresholds, scanner_gate_reject_reason

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILE = REPO_ROOT / "infra" / "docker-compose.prod.yml"
ENV_FILE = REPO_ROOT / "infra" / "env.production"
TOPICS_JSON = REPO_ROOT / "infra" / "pubsub" / "topics.json"
SUBSCRIPTIONS_JSON = REPO_ROOT / "infra" / "pubsub" / "subscriptions.json"
DEFAULT_HOST_GCP_CREDENTIALS = Path("/dev/shm/roboinvest/gcp-pubsub-sa.json")
GCP_CREDENTIALS_OP_REF = "op://roboinvest/production/GOOGLE_APPLICATION_CREDENTIALS_JSON"
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
    "market_regime",
)

TARGET_SERVICES = ("strategy-ai", "aggregator", "gateway")
CORE_SERVICES = (
    "strategy-ai",
    "aggregator",
    "gateway",
    "feature-engine",
    "strategy-rule",
    "oms-live",
    "oms-paper",
)

EXPECTED_ENV = {
    "AI_MAX_OUTPUT_TOKENS": "2048",
    "STRATEGIES_ENABLED": "relative_momentum",
    "LIVE_DAY_NEW_BUY_START_TIME": "09:15",
    "MAX_HOLD_MINUTES": "15",
    "MIN_CONFIDENCE_RULE_ONLY": "0.45",
    "MIN_CONFIDENCE_AI_ONLY": "0.5",
    "MIN_CONFIDENCE_CONSENSUS": "0.3",
    "SCAN_STATIC_MIN_TURNOVER_JPY": "200000000",
    "SCAN_STATIC_PRICE_MIN": "300",
    "SCAN_STATIC_PRICE_MAX": "5000",
    "SCAN_STATIC_MIN_LOT_SIZE": "100",
    "SCAN_STATIC_MAX_MIN_LOT_NOTIONAL_JPY": "500000",
    "SCAN_DYNAMIC_MAX_RISK_PENALTY": "1.5",
    "SCAN_DYNAMIC_MAX_VOLUME_SURGE": "2.1",
    "SCAN_DYNAMIC_MAX_MOMENTUM": "0.4",
    "ENTRY_VOLUME_RATIO_MIN": "",
    "ENTRY_MAX_SPREAD_BPS": "30",
    "ENTRY_MAX_SPREAD_TICKS": "2",
    "ENTRY_MIN_ASK_DEPTH_5": "1000",
    "ENTRY_MIN_BOOK_IMBALANCE_5": "-1.0",
    "ENTRY_MIN_MINUTES_FROM_OPEN": "15",
    "ENTRY_MAX_BOOK_AGE_SECONDS": "30",
    "ENTRY_MAX_PRICE": "5000",
    "BUY_TARGET_PCT": "",
    "BUY_TRAILING_STOP_PCT": "0.002",
    "PAPER_BUY_LIMIT_OFFSET_TICKS": "3",
    "PAPER_SYMBOL_ORDER_COOLDOWN_SECONDS": "300",
    "MARKET_REGIME_PAPER_GUARD_ENABLED": "true",
    "SOFT_LOSS_THROTTLE_GUARD_ENABLED": "true",
    "EXECUTION_GATE_GUARD_ENABLED": "true",
    "SCANNER_GATE_GUARD_ENABLED": "true",
    "SCANNER_GATE_MAX_RISK_PENALTY": "1.5",
    "SCANNER_GATE_MAX_VOLUME_SURGE": "2.1",
    "SCANNER_GATE_MAX_MOMENTUM": "0.4",
    "OMS_LIVE_STOP_MONITOR_ENABLED": "false",
    "OMS_LIVE_PUBSUB_SUBSCRIPTION_RAW_MARKET_DATA": "oms-live-raw-books",
    "OMS_PAPER_RAW_BOOK_DRAIN_MAX_BATCHES": "10",
    "PAPER_DAY_STOP_MONITOR_ENABLED": "true",
}

EXPECTED_CONTAINER_ENV = {
    ("oms-live", "PUBSUB_SUBSCRIPTION_RAW_MARKET_DATA"): "oms-live-raw-books",
    ("oms-live", "OMS_LIVE_STOP_MONITOR_ENABLED"): "false",
    ("oms-paper", "PUBSUB_SUBSCRIPTION_RAW_MARKET_DATA"): "oms-paper-raw-books",
    ("oms-paper", "PAPER_DAY_STOP_MONITOR_ENABLED"): "true",
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
        "--expected-trade-mode",
        choices=("live", "paper"),
        default="live",
        help="Expected TRADE_MODE and system_status.trade_mode.",
    )
    parser.add_argument(
        "--refresh-kabu-token",
        action="store_true",
        help=(
            "Clear the shared kabu token cache and restart oms-live/feeder before checks. "
            "Allowed only during the JST pre-open window unless --allow-market-hours-refresh "
            "is also set."
        ),
    )
    parser.add_argument(
        "--allow-market-hours-refresh",
        action="store_true",
        help="Allow --refresh-kabu-token outside the JST 05:00-09:00 pre-open window.",
    )
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


def _truncate_output(proc: subprocess.CompletedProcess[str]) -> str:
    return (proc.stderr or proc.stdout).strip()[:240]


def _materialize_gcp_credentials_from_1password(
    reporter: Reporter,
    args: argparse.Namespace,
    reason: str,
) -> Path | None:
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix="roboinvest-gcp-pubsub-sa-",
        suffix=".json",
        dir="/tmp",
        delete=False,
    ) as tmp:
        tmp_path = Path(tmp.name)
    try:
        os.chmod(tmp_path, 0o600)
        try:
            proc = _run(["op", "read", GCP_CREDENTIALS_OP_REF], timeout=args.timeout)
        except subprocess.TimeoutExpired:
            tmp_path.unlink(missing_ok=True)
            reporter.emit(
                "NG",
                "GOOGLE_APPLICATION_CREDENTIALS",
                f"{reason}; 1Password fallback timed out",
            )
            return None
        if proc.returncode != 0:
            tmp_path.unlink(missing_ok=True)
            reporter.emit(
                "NG",
                "GOOGLE_APPLICATION_CREDENTIALS",
                f"{reason}; 1Password fallback failed: {_truncate_output(proc)}",
            )
            return None
        try:
            json.loads(proc.stdout)
        except json.JSONDecodeError:
            tmp_path.unlink(missing_ok=True)
            reporter.emit(
                "NG",
                "GOOGLE_APPLICATION_CREDENTIALS",
                f"{reason}; 1Password fallback returned invalid JSON",
            )
            return None
        tmp_path.write_text(proc.stdout, encoding="utf-8")
        reporter.emit(
            "OK",
            "GOOGLE_APPLICATION_CREDENTIALS",
            f"{reason}; using temporary 1Password credential",
        )
        return tmp_path
    except OSError as exc:
        tmp_path.unlink(missing_ok=True)
        reporter.emit(
            "NG",
            "GOOGLE_APPLICATION_CREDENTIALS",
            f"{reason}; temporary credential failed: {exc}",
        )
        return None


def _resolve_gcp_credentials(
    reporter: Reporter,
    args: argparse.Namespace,
) -> tuple[Path | None, Path | None]:
    credentials = args.gcp_credentials
    is_default = credentials == DEFAULT_HOST_GCP_CREDENTIALS
    if credentials.exists() and os.access(credentials, os.R_OK):
        return credentials, None

    if credentials.exists():
        reason = f"not readable: {credentials}"
    else:
        reason = f"missing host file: {credentials}"

    if is_default:
        temp_credentials = _materialize_gcp_credentials_from_1password(reporter, args, reason)
        return temp_credentials, temp_credentials

    reporter.emit("NG", "GOOGLE_APPLICATION_CREDENTIALS", reason)
    return None, None


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _topic_path(project_id: str, topic: str) -> str:
    return pubsub_v1.PublisherClient.topic_path(project_id, topic)


def _subscription_path(project_id: str, subscription: str) -> str:
    return pubsub_v1.SubscriberClient.subscription_path(project_id, subscription)


def check_expected_env(reporter: Reporter, args: argparse.Namespace) -> None:
    reporter.section("production env")
    for key, expected in EXPECTED_ENV.items():
        actual = os.environ.get(key, "")
        if actual == expected:
            reporter.emit("OK", key, actual)
        elif actual:
            reporter.emit("NG", key, f"actual={actual} expected={expected}")
        else:
            reporter.emit("NG", key, f"missing expected={expected}")

    value = os.environ.get("TRADE_MODE", "")
    if value == args.expected_trade_mode:
        reporter.emit("OK", "TRADE_MODE", value)
    elif value:
        reporter.emit(
            "NG",
            "TRADE_MODE",
            f"actual={value} expected={args.expected_trade_mode}",
        )
    else:
        reporter.emit("NG", "TRADE_MODE", f"missing expected={args.expected_trade_mode}")

    for key in ("OMS_LIVE_DRY_RUN", "GEMINI_MODEL", "OMS_LIVE_MAX_QTY_PER_ORDER"):
        value = os.environ.get(key, "")
        reporter.emit("OK" if value else "WARN", key, value or "missing")


def refresh_kabu_token(reporter: Reporter, args: argparse.Namespace) -> None:
    reporter.section("kabu token refresh")
    if args.kabu_offline:
        reporter.emit("SKIP", "refresh", "kabu-offline")
        return

    now = datetime.now(ZoneInfo("Asia/Tokyo")).time()
    in_preopen = datetime_time(5, 0) <= now < datetime_time(9, 0)
    if not in_preopen and not args.allow_market_hours_refresh:
        reporter.emit(
            "NG",
            "pre-open window",
            "use --allow-market-hours-refresh to refresh outside 05:00-09:00 JST",
        )
        return

    unlink_script = (
        "import os; from pathlib import Path; "
        "p = Path(os.environ.get('KABU_TOKEN_CACHE_FILE', '/var/lib/kabu/token_cache.json')); "
        "p.unlink(missing_ok=True); print(p)"
    )
    proc = _run(
        _compose_cmd(args, "exec", "-T", "feeder", "python", "-c", unlink_script),
        timeout=args.timeout,
    )
    if proc.returncode != 0:
        reporter.emit("NG", "clear token cache", _truncate_output(proc))
        return
    reporter.emit("OK", "clear token cache", proc.stdout.strip())

    proc = _run(_compose_cmd(args, "restart", "oms-live", "feeder"), timeout=args.timeout)
    if proc.returncode != 0:
        reporter.emit("NG", "restart kabu services", _truncate_output(proc))
        return
    reporter.emit("OK", "restart kabu services", "oms-live, feeder")


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
        "feature-engine": ("MAX_HOLD_MINUTES",),
        "strategy-rule": (
            "STRATEGIES_ENABLED",
            "ENTRY_VOLUME_RATIO_MIN",
            "ENTRY_MAX_SPREAD_BPS",
            "ENTRY_MAX_SPREAD_TICKS",
            "ENTRY_MIN_ASK_DEPTH_5",
            "ENTRY_MIN_BOOK_IMBALANCE_5",
            "ENTRY_MIN_MINUTES_FROM_OPEN",
            "ENTRY_MAX_BOOK_AGE_SECONDS",
            "ENTRY_MAX_PRICE",
            "BUY_TARGET_PCT",
            "BUY_TRAILING_STOP_PCT",
        ),
        "gateway": (
            "LIVE_DAY_NEW_BUY_START_TIME",
            "LIVE_DAY_NEW_BUY_CUTOFF_TIME",
            "TRADE_MODE",
            "PAPER_BUY_LIMIT_OFFSET_TICKS",
            "PAPER_SYMBOL_ORDER_COOLDOWN_SECONDS",
            "MARKET_REGIME_PAPER_GUARD_ENABLED",
            "SOFT_LOSS_THROTTLE_GUARD_ENABLED",
            "EXECUTION_GATE_GUARD_ENABLED",
            "SCANNER_GATE_GUARD_ENABLED",
            "SCANNER_GATE_MAX_RISK_PENALTY",
            "SCANNER_GATE_MAX_VOLUME_SURGE",
            "SCANNER_GATE_MAX_MOMENTUM",
        ),
        "aggregator": (
            "MIN_CONFIDENCE_RULE_ONLY",
            "MIN_CONFIDENCE_AI_ONLY",
            "MIN_CONFIDENCE_CONSENSUS",
            "CONFLICT_POLICY",
        ),
        "oms-live": (
            "PUBSUB_SUBSCRIPTION_RAW_MARKET_DATA",
            "OMS_LIVE_STOP_MONITOR_ENABLED",
            "OMS_LIVE_DRY_RUN",
        ),
        "oms-paper": (
            "PUBSUB_SUBSCRIPTION_RAW_MARKET_DATA",
            "RAW_BOOK_DRAIN_MAX_BATCHES",
            "PAPER_DAY_STOP_MONITOR_ENABLED",
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
            expected = EXPECTED_CONTAINER_ENV.get((service, key), EXPECTED_ENV.get(key))
            if key == "TRADE_MODE":
                expected = args.expected_trade_mode
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
                    "OK" if trade_mode == args.expected_trade_mode else "NG",
                    "trade_mode",
                    f"{trade_mode} expected={args.expected_trade_mode}",
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

        _check_watchlist_gate(reporter, args, client)


def _check_watchlist_gate(
    reporter: Reporter, args: argparse.Namespace, client: httpx.Client
) -> None:
    valid_date = datetime.now(ZoneInfo("Asia/Tokyo")).date()
    resp = client.get(
        "/rest/v1/watchlist",
        params={
            "select": "symbol,selected_reasons",
            "valid_date": f"eq.{valid_date.isoformat()}",
            "order": "score.desc",
        },
    )
    if resp.status_code != 200:
        reporter.emit("NG", "watchlist today", f"HTTP {resp.status_code} {resp.text[:120]}")
        return

    rows = resp.json()
    if not isinstance(rows, list):
        reporter.emit("NG", "watchlist today", f"unexpected payload={type(rows).__name__}")
        return
    if not rows:
        reporter.emit("NG", "watchlist today", f"empty valid_date={valid_date.isoformat()}")
        return

    reporter.emit("OK", "watchlist today", f"{len(rows)} rows valid_date={valid_date.isoformat()}")

    pass_symbols: list[str] = []
    fail_counts: dict[str, int] = {}
    for row in rows:
        reasons = row.get("selected_reasons") if isinstance(row, dict) else None
        reason = scanner_gate_reject_reason(
            reasons if isinstance(reasons, dict) else None,
            _scanner_gate_thresholds_from_env(),
            reason_prefix="",
        )
        if reason is None:
            symbol = str(row.get("symbol", "")) if isinstance(row, dict) else ""
            if symbol:
                pass_symbols.append(symbol)
        else:
            fail_counts[reason] = fail_counts.get(reason, 0) + 1

    pass_count = len(pass_symbols)
    if pass_count == 0:
        reporter.emit("NG", "watchlist scanner gate", f"pass=0 fail={len(rows)}")
    elif fail_counts:
        detail = ", ".join(f"{key}={value}" for key, value in sorted(fail_counts.items()))
        reporter.emit("OK", "watchlist scanner gate", f"pass={pass_count} reject={detail}")
    else:
        reporter.emit("OK", "watchlist scanner gate", f"pass={pass_count}")

    _check_oms_live_allowed_symbols(reporter, args, pass_symbols)


def _check_oms_live_allowed_symbols(
    reporter: Reporter, args: argparse.Namespace, pass_symbols: list[str]
) -> None:
    proc = _run(
        _compose_cmd(
            args,
            "exec",
            "-T",
            "oms-live",
            "sh",
            "-c",
            'printf "%s\\n" "$OMS_LIVE_ALLOWED_SYMBOLS"',
        ),
        timeout=args.timeout,
    )
    if proc.returncode != 0:
        reporter.emit("NG", "oms-live allowed symbols", _truncate_output(proc))
        return

    raw_allowed = proc.stdout.strip()
    if not raw_allowed:
        reporter.emit("WARN", "oms-live allowed symbols", "missing")
        return

    allowed = sorted(symbol.strip() for symbol in raw_allowed.split(",") if symbol.strip())
    expected = sorted(pass_symbols)
    if allowed == expected:
        reporter.emit("OK", "oms-live allowed scanner gate", f"{len(allowed)} symbols")
        return

    missing = sorted(set(expected) - set(allowed))
    extra = sorted(set(allowed) - set(expected))
    detail_parts = [f"allowed={len(allowed)} expected={len(expected)}"]
    if missing:
        detail_parts.append(f"missing={','.join(missing[:8])}")
    if extra:
        detail_parts.append(f"extra={','.join(extra[:8])}")
    reporter.emit("NG", "oms-live allowed scanner gate", " ".join(detail_parts))


def _scanner_gate_thresholds_from_env() -> ScannerGateThresholds:
    return ScannerGateThresholds(
        max_risk_penalty=_env_decimal("SCAN_DYNAMIC_MAX_RISK_PENALTY"),
        max_volume_surge=_env_decimal("SCAN_DYNAMIC_MAX_VOLUME_SURGE"),
        max_momentum=_env_decimal("SCAN_DYNAMIC_MAX_MOMENTUM"),
    )


def _env_decimal(name: str) -> Decimal | None:
    value = os.environ.get(name, "").strip()
    if not value:
        return None
    return Decimal(value)


def _load_topics() -> list[str]:
    payload = _load_json(TOPICS_JSON)
    return [str(topic) for topic in payload["topics"]]


def _load_subscriptions() -> list[tuple[str, str, str | None]]:
    payload = _load_json(SUBSCRIPTIONS_JSON)
    return [
        (
            str(item["name"]),
            str(item["topic"]),
            str(item["filter"]) if item.get("filter") is not None else None,
        )
        for item in payload["subscriptions"]
    ]


def check_pubsub(reporter: Reporter, args: argparse.Namespace) -> None:
    reporter.section("managed Pub/Sub")
    project_id = os.environ.get("PUBSUB_PROJECT_ID", "")
    if not project_id:
        reporter.emit("NG", "PUBSUB_PROJECT_ID", "missing")
        return
    credentials, cleanup_credentials = _resolve_gcp_credentials(reporter, args)
    if credentials is None:
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

            for name, topic, expected_filter in _load_subscriptions():
                try:
                    sub = subscriber.get_subscription(
                        request={"subscription": _subscription_path(project_id, name)},
                        timeout=args.timeout,
                    )
                    actual = sub.topic.rsplit("/", 1)[-1]
                    actual_filter = sub.filter or None
                    if actual == topic:
                        reporter.emit("OK", f"sub:{name}", f"-> {topic}")
                    else:
                        reporter.emit("NG", f"sub:{name}", f"actual={actual} expected={topic}")
                    if actual_filter == expected_filter:
                        if expected_filter is not None:
                            reporter.emit("OK", f"sub-filter:{name}", expected_filter)
                    else:
                        reporter.emit(
                            "NG",
                            f"sub-filter:{name}",
                            f"actual={actual_filter or '<none>'} "
                            f"expected={expected_filter or '<none>'}",
                        )
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
        if cleanup_credentials is not None:
            cleanup_credentials.unlink(missing_ok=True)


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
    latest_status = ""
    latest_detail = ""
    for line in text.splitlines():
        if "kabusapi/token" in line and "200 OK" in line:
            latest_status = "OK"
            latest_detail = "token 200"
        elif "kabusapi/unregister/all" in line and "200 OK" in line:
            latest_status = "OK"
            latest_detail = "unregister/all 200"
        elif "kabusapi/register" in line and "200 OK" in line:
            latest_status = "OK"
            latest_detail = "register 200"
        elif "HTTP 502" in line or "Bad Gateway" in line:
            latest_status = "BAD_GATEWAY"
            latest_detail = "HTTP 502"
        elif "HTTP/1.1 401" in line or "APIキー不一致" in line:
            latest_status = "AUTH"
            latest_detail = "HTTP 401"
        elif "Traceback" in line:
            latest_status = "TRACEBACK"
            latest_detail = "traceback present"

    if latest_status == "OK":
        reporter.emit("OK", "feeder kabu", latest_detail)
    elif latest_status == "BAD_GATEWAY":
        reporter.emit("WARN" if args.kabu_offline else "NG", "kabu websocket", latest_detail)
    elif latest_status == "AUTH":
        reporter.emit("WARN" if args.kabu_offline else "NG", "kabu auth", latest_detail)
    elif latest_status == "TRACEBACK":
        reporter.emit("WARN" if args.kabu_offline else "NG", "feeder logs", latest_detail)
    else:
        reporter.emit("OK", "feeder logs", "no recent kabu error")


def main() -> int:
    args = parse_args()
    reporter = Reporter(quiet=args.quiet)

    check_expected_env(reporter, args)
    if args.refresh_kabu_token:
        refresh_kabu_token(reporter, args)
    check_compose(reporter, args)
    check_container_env(reporter, args)
    check_supabase(reporter, args)
    check_pubsub(reporter, args)
    check_feeder_logs(reporter, args)
    reporter.summary()
    return 1 if reporter.failed else 0


if __name__ == "__main__":
    sys.exit(main())
