#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import httpx
from strategy_rule.event_paper.artifact import (
    EventArtifactError,
    EventPaperArtifact,
    load_event_paper_artifact,
)

EVENT_CAPTURE_SCORE = 0
DEFAULT_MAX_SYMBOLS = 10


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add event paper candidates to Supabase watchlist for minute-data capture."
    )
    parser.add_argument("--candidates-json", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--valid-date", type=date.fromisoformat)
    parser.add_argument("--max-symbols", type=int, default=DEFAULT_MAX_SYMBOLS)
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="Replace same-date scanner rows so registered candidates stay capture-only.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()

    try:
        artifact = load_event_paper_artifact(args.candidates_json).artifact
        if artifact.candidates:
            artifact.validate_target_date(artifact.candidates[0].entry_date)
    except EventArtifactError as exc:
        print(f"unsafe event candidate artifact: {exc}", file=sys.stderr)
        return 2
    rows = build_watchlist_rows(
        artifact,
        valid_date=args.valid_date,
        max_symbols=args.max_symbols,
    )
    inserted: list[dict[str, Any]] = []
    skipped_existing: list[dict[str, Any]] = []
    replaced_existing: list[dict[str, Any]] = []
    if not args.dry_run and rows:
        url = os.environ.get("SUPABASE_URL", "").strip()
        key = os.environ.get("SUPABASE_SECRET_KEY", "").strip()
        if not url or not key:
            print("SUPABASE_URL / SUPABASE_SECRET_KEY missing", file=sys.stderr)
            return 2
        with httpx.Client(
            base_url=url.rstrip("/"),
            timeout=args.timeout,
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
        ) as client:
            if args.replace_existing:
                inserted, replaced_existing = upsert_capture_watchlist_rows(client, rows)
            else:
                inserted, skipped_existing = upsert_missing_watchlist_rows(client, rows)

    result = {
        "mode": "dry_run" if args.dry_run else "upsert",
        "candidate_count": len(artifact.candidates),
        "planned_count": len(rows),
        "inserted_count": len(inserted),
        "skipped_existing_count": len(skipped_existing),
        "replaced_existing_count": len(replaced_existing),
        "planned": rows,
        "inserted": inserted,
        "skipped_existing": skipped_existing,
        "replaced_existing": replaced_existing,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(
        "event_candidate_watchlist "
        f"mode={result['mode']} planned={len(rows)} inserted={len(inserted)} "
        f"skipped_existing={len(skipped_existing)} "
        f"replaced_existing={len(replaced_existing)} output={args.output_json}"
    )
    return 0


def build_watchlist_rows(
    artifact: EventPaperArtifact,
    *,
    valid_date: date | None,
    max_symbols: int,
) -> list[dict[str, Any]]:
    if max_symbols <= 0:
        raise ValueError("max_symbols must be positive")
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for candidate in artifact.candidates:
        symbol = candidate.symbol
        row_valid_date = (
            valid_date.isoformat() if valid_date is not None else candidate.entry_date.isoformat()
        )
        key = (row_valid_date, symbol)
        by_key.setdefault(
            key,
            {
                "symbol": symbol,
                "valid_date": row_valid_date,
                "symbol_name": candidate.symbol_name,
                "score": EVENT_CAPTURE_SCORE,
                "selected_reasons": {
                    "reasons": ["event_capture"],
                    "event_capture": True,
                    "candidate_id": candidate.candidate_id,
                    "cluster_id": candidate.cluster_id,
                    "signal_date": candidate.signal_date.isoformat(),
                    "entry_date": candidate.entry_date.isoformat(),
                },
            },
        )
    return sorted(by_key.values(), key=lambda row: (row["valid_date"], row["symbol"]))[:max_symbols]


def upsert_missing_watchlist_rows(
    client: httpx.Client,
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    inserted: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for valid_date, date_rows in _group_by_valid_date(rows).items():
        existing = fetch_existing_symbols(client, valid_date=valid_date)
        missing = [row for row in date_rows if row["symbol"] not in existing]
        skipped.extend([row for row in date_rows if row["symbol"] in existing])
        if missing:
            upsert_watchlist_rows(client, missing)
            inserted.extend(missing)
    return inserted, skipped


def upsert_capture_watchlist_rows(
    client: httpx.Client,
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    inserted: list[dict[str, Any]] = []
    replaced: list[dict[str, Any]] = []
    for valid_date, date_rows in _group_by_valid_date(rows).items():
        existing = fetch_existing_symbols(client, valid_date=valid_date)
        inserted.extend([row for row in date_rows if row["symbol"] not in existing])
        replaced.extend([row for row in date_rows if row["symbol"] in existing])
        upsert_watchlist_rows(client, date_rows)
    return inserted, replaced


def fetch_existing_symbols(client: httpx.Client, *, valid_date: str) -> set[str]:
    resp = client.get(
        "/rest/v1/watchlist",
        params={
            "select": "symbol",
            "valid_date": f"eq.{valid_date}",
        },
    )
    _raise_for_status(resp, table="watchlist", action="read")
    payload = resp.json()
    if not isinstance(payload, list):
        raise RuntimeError(f"unexpected watchlist payload: {payload!r}")
    return {str(row["symbol"]) for row in payload if row.get("symbol")}


def upsert_watchlist_rows(client: httpx.Client, rows: list[dict[str, Any]]) -> None:
    resp = client.post(
        "/rest/v1/watchlist",
        params={"on_conflict": "symbol,valid_date"},
        headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
        json=rows,
    )
    _raise_for_status(resp, table="watchlist", action="upsert")


def _group_by_valid_date(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["valid_date"])].append(row)
    return dict(grouped)


def _raise_for_status(resp: httpx.Response, *, table: str, action: str) -> None:
    if resp.status_code >= 300:
        raise RuntimeError(
            f"{action} failed: table={table} status={resp.status_code} body={resp.text[:200]}"
        )


if __name__ == "__main__":
    raise SystemExit(main())
