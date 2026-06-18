#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "httpx>=0.27",
# ]
# ///
"""Unified paper checkpoint report for intraday observation.

Run under resolved production env:

    set -a && . infra/.op.service-account.env && set +a
    op run --env-file infra/env.production -- \\
      uv run python scripts/report-paper-checkpoint.py --checkpoint open
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

JST = ZoneInfo("Asia/Tokyo")
DEFAULT_COMPOSE_FILE = Path("infra/docker-compose.prod.yml")
DEFAULT_ENV_FILE = Path("infra/env.production")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", type=_parse_date, default=datetime.now(JST).date())
    parser.add_argument(
        "--checkpoint",
        choices=("open", "midday", "close", "adhoc"),
        default="adhoc",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-rows", type=int, default=10)
    parser.add_argument("--archive-dir", type=Path, default=None)
    parser.add_argument("--compose-file", type=Path, default=DEFAULT_COMPOSE_FILE)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--no-compose", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must be YYYY-MM-DD") from exc


def _date_bounds_jst(trading_date: date) -> tuple[datetime, datetime]:
    start = datetime.combine(trading_date, time.min, JST).astimezone(UTC)
    end = datetime.combine(trading_date + timedelta(days=1), time.min, JST).astimezone(UTC)
    return start, end


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _get_all(
    client: httpx.Client,
    path: str,
    params: dict[str, str],
    *,
    page_size: int = 1000,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        resp = client.get(
            path,
            params=params,
            headers={"Range": f"{offset}-{offset + page_size - 1}"},
        )
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, list):
            raise RuntimeError(f"unexpected response for {path}: {payload!r}")
        rows.extend(payload)
        if len(payload) < page_size:
            return rows
        offset += page_size


def _fetch_day_rows(
    client: httpx.Client,
    *,
    table: str,
    timestamp_field: str,
    trading_date: date,
    select: str,
    order: str,
) -> list[dict[str, Any]]:
    start, end = _date_bounds_jst(trading_date)
    return _get_all(
        client,
        f"/rest/v1/{table}",
        {
            "select": select,
            "and": f"({timestamp_field}.gte.{_iso(start)},{timestamp_field}.lt.{_iso(end)})",
            "order": order,
        },
    )


def _fetch_exact_date_rows(
    client: httpx.Client,
    *,
    table: str,
    date_field: str,
    trading_date: date,
    select: str,
    order: str,
) -> list[dict[str, Any]]:
    return _get_all(
        client,
        f"/rest/v1/{table}",
        {
            "select": select,
            date_field: f"eq.{trading_date.isoformat()}",
            "order": order,
        },
    )


def _counter(rows: list[dict[str, Any]], *fields: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        counts[" / ".join(str(row.get(field, "")) for field in fields)] += 1
    return dict(sorted(counts.items()))


def _top_symbols(rows: list[dict[str, Any]], *, limit: int = 10) -> list[dict[str, Any]]:
    counts = Counter(str(row.get("symbol", "")) for row in rows if row.get("symbol"))
    return [{"symbol": symbol, "count": count} for symbol, count in counts.most_common(limit)]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def _find_archive_dir(trading_date: date) -> Path | None:
    out_dir = Path("out")
    if not out_dir.exists():
        return None
    rel = Path("orders") / "trade_mode=paper" / f"date={trading_date.isoformat()}" / "orders.jsonl"
    candidates = [
        path for path in out_dir.glob("paper-archive-*") if path.is_dir() and (path / rel).exists()
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0]


def _local_archive_orders(archive_dir: Path | None, trading_date: date) -> list[dict[str, Any]]:
    resolved = archive_dir or _find_archive_dir(trading_date)
    if resolved is None:
        return []
    path = (
        resolved
        / "orders"
        / "trade_mode=paper"
        / f"date={trading_date.isoformat()}"
        / "orders.jsonl"
    )
    return _read_jsonl(path)


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


def _run_compose(args: argparse.Namespace, *extra: str) -> tuple[int, str, str]:
    proc = subprocess.run(
        _compose_cmd(args, *extra),
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        timeout=args.timeout,
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _container_archive_orders(
    args: argparse.Namespace, trading_date: date
) -> tuple[list[dict[str, Any]], str | None]:
    path = f"/data/orders/trade_mode=paper/date={trading_date.isoformat()}/orders.jsonl"
    code, stdout, stderr = _run_compose(
        args,
        "exec",
        "-T",
        "gateway",
        "sh",
        "-c",
        f"test -f {path} && cat {path} || true",
    )
    if code != 0:
        return [], (stderr or stdout).strip()[:240]
    rows: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows, None


def _decode_order_messages_from_log_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    # Reserved for future use if gateway publish logs start embedding order JSON.
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return []
    rows: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict) or not isinstance(message.get("data"), str):
            continue
        try:
            decoded = base64.b64decode(message["data"]).decode("utf-8")
            order = json.loads(decoded)
        except Exception:
            continue
        if isinstance(order, dict):
            rows.append(order)
    return rows


def _extract_json_from_log_line(line: str) -> dict[str, Any] | None:
    candidate = line.split("|", 1)[1].strip() if "|" in line else line.strip()
    if not candidate.startswith("{"):
        return None
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _compose_log_summary(
    args: argparse.Namespace, trading_date: date
) -> tuple[dict[str, Any], str | None]:
    start, _end = _date_bounds_jst(trading_date)
    code, stdout, stderr = _run_compose(
        args,
        "logs",
        "--since",
        _iso(start),
        "gateway",
        "oms-paper",
    )
    if code != 0:
        return {}, (stderr or stdout).strip()[:240]

    event_counts: Counter[str] = Counter()
    gateway_reject_reasons: Counter[str] = Counter()
    paper_no_fill_reasons: Counter[str] = Counter()
    latest_market_summary: dict[str, Any] | None = None
    decoded_order_rows = 0
    for line in stdout.splitlines():
        payload = _extract_json_from_log_line(line)
        if payload is None:
            continue
        event = payload.get("event")
        if isinstance(event, str):
            event_counts[event] += 1
        if event == "signal_rejected":
            reason = payload.get("reason")
            if isinstance(reason, str):
                gateway_reject_reasons[reason] += 1
        elif event == "paper_order_no_fill":
            reason = payload.get("reason")
            if isinstance(reason, str):
                paper_no_fill_reasons[reason] += 1
        elif event == "market_data_summary":
            latest_market_summary = payload
        decoded_order_rows += len(_decode_order_messages_from_log_payload(payload))

    return {
        "event_counts": dict(sorted(event_counts.items())),
        "gateway_reject_reasons": dict(sorted(gateway_reject_reasons.items())),
        "paper_no_fill_reasons": dict(sorted(paper_no_fill_reasons.items())),
        "paper_filled_events": event_counts.get("paper_order_filled", 0),
        "order_published_events": event_counts.get("order_published", 0),
        "decoded_order_rows": decoded_order_rows,
        "latest_market_data_summary": _compact_market_summary(latest_market_summary),
    }, None


def _compact_market_summary(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if payload is None:
        return None
    keys = (
        "timestamp",
        "books_pulled",
        "books_applied",
        "orders_pulled",
        "filled",
        "no_fills",
        "latest_book_age_seconds",
        "window_seconds",
    )
    return {key: payload.get(key) for key in keys if key in payload}


def _build_result(args: argparse.Namespace) -> dict[str, Any]:
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SECRET_KEY", "")
    if not url or not key:
        raise SystemExit("SUPABASE_URL / SUPABASE_SECRET_KEY missing")

    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    with httpx.Client(base_url=url, headers=headers, timeout=args.timeout) as client:
        status_rows = _get_all(
            client,
            "/rest/v1/system_status",
            {"select": "trade_mode,is_trading_allowed,daily_pnl,updated_at", "id": "eq.1"},
        )
        watchlist = _fetch_exact_date_rows(
            client,
            table="watchlist",
            date_field="valid_date",
            trading_date=args.date,
            select="symbol,symbol_name,score,selected_reasons,created_at",
            order="score.desc",
        )
        strategy_logs = _fetch_day_rows(
            client,
            table="strategy_logs",
            timestamp_field="created_at",
            trading_date=args.date,
            select="created_at,source,symbol,action,confidence",
            order="created_at.asc",
        )
        aggregator_logs = _fetch_day_rows(
            client,
            table="aggregator_logs",
            timestamp_field="created_at",
            trading_date=args.date,
            select="created_at,symbol,action,confidence,signal_source,signal_id",
            order="created_at.asc",
        )
        paper_trades = _fetch_day_rows(
            client,
            table="trades_paper",
            timestamp_field="executed_at",
            trading_date=args.date,
            select="executed_at,symbol,side,quantity,price,signal_source,unified_signal_id",
            order="executed_at.asc",
        )
        paper_positions = _get_all(
            client,
            "/rest/v1/positions",
            {
                "select": (
                    "symbol,quantity,entry_price,current_price,"
                    "unrealized_pnl,holding_type,opened_at"
                ),
                "trade_type": "eq.paper",
                "order": "symbol.asc",
            },
        )

    local_orders = _local_archive_orders(args.archive_dir, args.date)
    container_orders: list[dict[str, Any]] = []
    compose_warnings: list[str] = []
    compose_logs: dict[str, Any] = {}
    if not args.no_compose:
        container_orders, warning = _container_archive_orders(args, args.date)
        if warning:
            compose_warnings.append(f"container_archive: {warning}")
        compose_logs, warning = _compose_log_summary(args, args.date)
        if warning:
            compose_warnings.append(f"logs: {warning}")

    archive_orders = container_orders or local_orders
    archive_buy_orders = [row for row in archive_orders if row.get("side") == "BUY"]

    return {
        "date_jst": args.date.isoformat(),
        "checkpoint": args.checkpoint,
        "generated_at": datetime.now(JST).isoformat(),
        "system_status": status_rows[0] if status_rows else None,
        "watchlist": {
            "count": len(watchlist),
            "top": [
                {
                    "symbol": row.get("symbol"),
                    "name": row.get("symbol_name"),
                    "score": row.get("score"),
                }
                for row in watchlist[: args.max_rows]
            ],
        },
        "signals": {
            "strategy_total": len(strategy_logs),
            "strategy_by_source_action": _counter(strategy_logs, "source", "action"),
            "aggregator_total": len(aggregator_logs),
            "aggregator_by_source_action": _counter(aggregator_logs, "signal_source", "action"),
            "top_aggregator_buy_symbols": _top_symbols(
                [row for row in aggregator_logs if row.get("action") == "BUY"],
                limit=args.max_rows,
            ),
        },
        "orders": {
            "archive_source": "container"
            if container_orders
            else "local"
            if local_orders
            else "none",
            "archived_total": len(archive_orders),
            "archived_buy": len(archive_buy_orders),
            "archived_by_source_side": _counter(archive_orders, "signal_source", "side"),
            "top_archived_buy_symbols": _top_symbols(archive_buy_orders, limit=args.max_rows),
        },
        "paper_execution": {
            "trades_paper_total": len(paper_trades),
            "trades_paper_by_source_side": _counter(paper_trades, "signal_source", "side"),
            "open_paper_positions": len(paper_positions),
            "open_paper_position_symbols": [row.get("symbol") for row in paper_positions],
        },
        "logs": compose_logs,
        "warnings": compose_warnings,
    }


def _print_counts(title: str, counts: dict[str, int]) -> None:
    print(title)
    if not counts:
        print("  none")
        return
    for key, count in counts.items():
        print(f"  {key}: {count}")


def _print_result(result: dict[str, Any], *, max_rows: int) -> None:
    print(f"== Paper checkpoint {result['checkpoint']} {result['date_jst']} JST ==")
    status = result.get("system_status") or {}
    print(
        "system_status: "
        f"trade_mode={status.get('trade_mode')} "
        f"allowed={status.get('is_trading_allowed')} "
        f"daily_pnl={status.get('daily_pnl')} "
        f"updated_at={status.get('updated_at')}"
    )
    print()

    watchlist = result["watchlist"]
    print(f"watchlist_count={watchlist['count']}")
    if watchlist["top"]:
        print("watchlist_top")
        for row in watchlist["top"][:max_rows]:
            print(f"  {row['symbol']} {row['name']} score={row['score']}")
    print()

    signals = result["signals"]
    print(f"strategy_total={signals['strategy_total']}")
    _print_counts("strategy by source/action", signals["strategy_by_source_action"])
    print(f"aggregator_total={signals['aggregator_total']}")
    _print_counts("aggregator by source/action", signals["aggregator_by_source_action"])
    print("top aggregator BUY symbols")
    for row in signals["top_aggregator_buy_symbols"]:
        print(f"  {row['symbol']}: {row['count']}")
    if not signals["top_aggregator_buy_symbols"]:
        print("  none")
    print()

    orders = result["orders"]
    print(
        "orders: "
        f"source={orders['archive_source']} "
        f"archived_total={orders['archived_total']} "
        f"archived_buy={orders['archived_buy']}"
    )
    _print_counts("archived orders by source/side", orders["archived_by_source_side"])
    print("top archived BUY symbols")
    for row in orders["top_archived_buy_symbols"]:
        print(f"  {row['symbol']}: {row['count']}")
    if not orders["top_archived_buy_symbols"]:
        print("  none")
    print()

    paper = result["paper_execution"]
    print(
        "paper_execution: "
        f"trades_paper_total={paper['trades_paper_total']} "
        f"open_positions={paper['open_paper_positions']} "
        f"symbols={paper['open_paper_position_symbols']}"
    )
    _print_counts("trades_paper by source/side", paper["trades_paper_by_source_side"])
    print()

    logs = result.get("logs") or {}
    if logs:
        print("compose log events")
        _print_counts("event counts", logs.get("event_counts", {}))
        _print_counts("gateway reject reasons", logs.get("gateway_reject_reasons", {}))
        _print_counts("paper no_fill reasons", logs.get("paper_no_fill_reasons", {}))
        print(f"paper_filled_events={logs.get('paper_filled_events', 0)}")
        print(f"order_published_events={logs.get('order_published_events', 0)}")
        print(f"latest_market_data_summary={logs.get('latest_market_data_summary')}")
    else:
        print("compose log events: unavailable")
    if result["warnings"]:
        print()
        print("warnings")
        for warning in result["warnings"]:
            print(f"  {warning}")


def main() -> int:
    args = parse_args()
    result = _build_result(args)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    _print_result(result, max_rows=args.max_rows)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except httpx.HTTPError as exc:
        print(f"Supabase request failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
