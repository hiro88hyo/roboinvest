#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "httpx>=0.27",
# ]
# ///
"""Prepare Cloud Supabase for a risk-off paper trading day.

Run under resolved production env:

    set -a && . infra/.op.service-account.env && set +a
    op run --env-file infra/env.production -- \
      uv run python scripts/prepare-risk-off-paper-day.py --apply
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from typing import Any

import httpx


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="patch system_status to paper/day/allowed"
    )
    parser.add_argument(
        "--allow-live-positions",
        action="store_true",
        help="allow --apply even when live positions exist",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser.parse_args()


def _get(client: httpx.Client, path: str, params: dict[str, str]) -> list[dict[str, Any]]:
    resp = client.get(path, params=params)
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, list):
        raise RuntimeError(f"unexpected response for {path}: {payload!r}")
    return payload


def _print_positions(label: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        print(f"{label}: empty")
        return
    details = ", ".join(
        f"{row.get('symbol')} {row.get('side')} qty={row.get('quantity')} "
        f"pnl={row.get('unrealized_pnl')}"
        for row in rows
    )
    print(f"{label}: {details}")


def main() -> int:
    args = parse_args()
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SECRET_KEY", "")
    if not url or not key:
        print("SUPABASE_URL / SUPABASE_SECRET_KEY missing", file=sys.stderr)
        return 2

    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    with httpx.Client(base_url=url, headers=headers, timeout=args.timeout) as client:
        system_rows = _get(
            client,
            "/rest/v1/system_status",
            {
                "id": "eq.1",
                "select": (
                    "id,is_trading_allowed,trade_mode,trading_style,"
                    "daily_pnl,daily_loss_limit,updated_at"
                ),
            },
        )
        if not system_rows:
            print("system_status id=1 missing", file=sys.stderr)
            return 1

        live_positions = _get(
            client,
            "/rest/v1/positions",
            {
                "trade_type": "eq.live",
                "select": "symbol,quantity,side,holding_type,unrealized_pnl,opened_at",
                "order": "symbol.asc",
            },
        )
        paper_positions = _get(
            client,
            "/rest/v1/positions",
            {
                "trade_type": "eq.paper",
                "select": "symbol,quantity,side,holding_type,unrealized_pnl,opened_at",
                "order": "symbol.asc",
            },
        )

        print(f"system_status: {system_rows[0]}")
        _print_positions("live_positions", live_positions)
        _print_positions("paper_positions", paper_positions)

        if live_positions and not args.allow_live_positions:
            print(
                "Refusing to switch to paper while live positions exist. "
                "Close/reconcile live positions first, or pass --allow-live-positions.",
                file=sys.stderr,
            )
            return 1

        if not args.apply:
            print("dry-run: no changes applied")
            return 0

        stamp = datetime.now(UTC).isoformat()
        resp = client.patch(
            "/rest/v1/system_status",
            params={"id": "eq.1"},
            json={
                "is_trading_allowed": True,
                "trade_mode": "paper",
                "trading_style": "day",
                "updated_at": stamp,
            },
            headers={"Prefer": "return=representation"},
        )
        resp.raise_for_status()
        print(f"updated system_status: {resp.json()}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
