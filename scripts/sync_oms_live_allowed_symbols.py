from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from trade_contracts.scanner_gate import ScannerGateThresholds, scanner_gate_reject_reason


@dataclass(frozen=True, slots=True)
class SyncResult:
    status: str
    symbols: list[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync OMS_LIVE_ALLOWED_SYMBOLS from scanner-gated watchlist rows.",
    )
    parser.add_argument("valid_date", type=date.fromisoformat)
    parser.add_argument("--env-file", type=Path, default=Path("infra/env.production"))
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser.parse_args()


def env_decimal(name: str) -> Decimal | None:
    value = os.environ.get(name, "").strip()
    return None if not value else Decimal(value)


def thresholds_from_env() -> ScannerGateThresholds:
    return ScannerGateThresholds(
        max_risk_penalty=env_decimal("SCAN_DYNAMIC_MAX_RISK_PENALTY"),
        max_volume_surge=env_decimal("SCAN_DYNAMIC_MAX_VOLUME_SURGE"),
        max_momentum=env_decimal("SCAN_DYNAMIC_MAX_MOMENTUM"),
    )


def gate_pass_symbols(
    rows: list[dict[str, Any]],
    thresholds: ScannerGateThresholds,
) -> list[str]:
    return [
        str(row["symbol"])
        for row in rows
        if scanner_gate_reject_reason(row.get("selected_reasons") or {}, thresholds) is None
    ]


def fetch_watchlist_rows(*, valid_date: date, timeout: float) -> list[dict[str, Any]]:
    url = os.environ["SUPABASE_URL"].rstrip("/")
    key = os.environ["SUPABASE_SECRET_KEY"]
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    params = {
        "select": "symbol,selected_reasons",
        "valid_date": f"eq.{valid_date.isoformat()}",
        "order": "symbol.asc",
    }
    with httpx.Client(base_url=url, headers=headers, timeout=timeout) as client:
        response = client.get("/rest/v1/watchlist", params=params)
        response.raise_for_status()
        rows = response.json()
    if not isinstance(rows, list):
        raise TypeError(f"unexpected watchlist payload: {type(rows).__name__}")
    return rows


def update_env_file(path: Path, symbols: list[str]) -> SyncResult:
    symbol_csv = ",".join(symbols)
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if not line.startswith("OMS_LIVE_ALLOWED_SYMBOLS="):
            continue
        if line == f"OMS_LIVE_ALLOWED_SYMBOLS={symbol_csv}":
            return SyncResult(status="unchanged", symbols=symbols)
        lines[index] = f"OMS_LIVE_ALLOWED_SYMBOLS={symbol_csv}"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return SyncResult(status="updated", symbols=symbols)
    raise ValueError(f"OMS_LIVE_ALLOWED_SYMBOLS line not found in {path}")


def sync(valid_date: date, *, env_file: Path, timeout: float) -> SyncResult:
    rows = fetch_watchlist_rows(valid_date=valid_date, timeout=timeout)
    if not rows:
        return SyncResult(status="skipped", symbols=[])

    symbols = gate_pass_symbols(rows, thresholds_from_env())
    if not symbols:
        raise RuntimeError(
            f"scanner gate rejected all watchlist rows for {valid_date.isoformat()}: "
            f"rows={len(rows)}"
        )
    return update_env_file(env_file, symbols)


def main() -> int:
    args = parse_args()
    result = sync(args.valid_date, env_file=args.env_file, timeout=args.timeout)
    if result.status == "skipped":
        print(f"SKIPPED OMS_LIVE_ALLOWED_SYMBOLS watchlist empty date={args.valid_date}")
    elif result.status == "unchanged":
        print(f"UNCHANGED OMS_LIVE_ALLOWED_SYMBOLS count={len(result.symbols)}")
    else:
        print(f"UPDATED OMS_LIVE_ALLOWED_SYMBOLS count={len(result.symbols)}")
    print(f"SYNC_STATUS={result.status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
