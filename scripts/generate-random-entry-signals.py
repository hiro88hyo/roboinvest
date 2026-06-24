#!/usr/bin/env python3
"""Generate random StrategySignal JSONL from archived ProcessedFeatures.

The baseline keeps execution and exit assumptions explicit, and randomizes only
entry timing/symbol selection from rows that pass the same practical filters.
"""

from __future__ import annotations

import argparse
import json
import random
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from trade_contracts.enums import Action, SignalSource
from trade_contracts.signal import EXECUTION_FIELD_NAMES, StrategySignal


@dataclass(frozen=True, slots=True)
class Candidate:
    raw: dict[str, Any]
    timestamp: datetime
    symbol: str
    price: Decimal
    vwap: Decimal
    risk_bps: Decimal


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--features", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--samples", type=int, required=True)
    parser.add_argument("--max-spread-bps", type=Decimal, default=Decimal("10"))
    parser.add_argument("--max-spread-ticks", type=Decimal, default=Decimal("1"))
    parser.add_argument("--min-ask-depth-5", type=int, default=1000)
    parser.add_argument("--min-minutes-from-open", type=int, default=15)
    parser.add_argument("--min-minutes-to-close", type=int, default=60)
    parser.add_argument("--max-book-age-seconds", type=Decimal, default=Decimal("300"))
    parser.add_argument("--max-price", type=Decimal, default=None)
    parser.add_argument("--min-vwap-distance-bps", type=Decimal, default=Decimal("0"))
    parser.add_argument("--max-vwap-distance-bps", type=Decimal, default=Decimal("160"))
    parser.add_argument("--max-stop-risk-bps", type=Decimal, default=Decimal("160"))
    parser.add_argument("--target-r-multiple", type=Decimal, default=Decimal("1.5"))
    parser.add_argument("--one-per-symbol", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()

    candidates = read_candidates(args)
    rng = random.Random(args.seed)
    if args.one_per_symbol:
        candidates = sample_one_per_symbol(candidates, rng)
    rng.shuffle(candidates)
    selected = sorted(candidates[: args.samples], key=lambda item: (item.timestamp, item.symbol))
    write_signals(selected, args.output, args.target_r_multiple)
    print(
        "random_entry_signals "
        f"seed={args.seed} candidates={len(candidates)} selected={len(selected)} "
        f"output={args.output}"
    )
    return 0


def read_candidates(args: argparse.Namespace) -> list[Candidate]:
    out: list[Candidate] = []
    with args.features.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            if not line.strip():
                continue
            raw = json.loads(line)
            try:
                candidate = make_candidate(raw, args)
            except Exception as exc:
                raise ValueError(f"invalid row path={args.features} line={line_no}") from exc
            if candidate is not None:
                out.append(candidate)
    return out


def make_candidate(raw: dict[str, Any], args: argparse.Namespace) -> Candidate | None:
    price = to_decimal(raw.get("price"))
    vwap = to_decimal(raw.get("vwap"))
    if price is None or vwap is None or price <= 0 or vwap <= 0:
        return None
    if args.max_price is not None and price > args.max_price:
        return None
    vwap_distance_bps = ((price - vwap) / vwap) * Decimal("10000")
    if vwap_distance_bps < args.min_vwap_distance_bps:
        return None
    if vwap_distance_bps > args.max_vwap_distance_bps:
        return None
    risk_bps = ((price - vwap) / price) * Decimal("10000")
    if risk_bps <= 0 or risk_bps > args.max_stop_risk_bps:
        return None
    if not passes_exec_filters(raw, args):
        return None
    return Candidate(
        raw=raw,
        timestamp=datetime.fromisoformat(raw["timestamp"]),
        symbol=str(raw["symbol"]),
        price=price,
        vwap=vwap,
        risk_bps=risk_bps,
    )


def passes_exec_filters(raw: dict[str, Any], args: argparse.Namespace) -> bool:
    spread_bps = to_decimal(raw.get("spread_bps"))
    spread_ticks = to_decimal(raw.get("spread_ticks"))
    ask_depth_5 = to_int(raw.get("ask_depth_5"))
    minutes_from_open = to_int(raw.get("minutes_from_open"))
    minutes_to_close = to_int(raw.get("minutes_to_close"))
    if spread_bps is None or spread_bps > args.max_spread_bps:
        return False
    if spread_ticks is None or spread_ticks > args.max_spread_ticks:
        return False
    if ask_depth_5 is None or ask_depth_5 < args.min_ask_depth_5:
        return False
    if minutes_from_open is None or minutes_from_open < args.min_minutes_from_open:
        return False
    if minutes_to_close is None or minutes_to_close < args.min_minutes_to_close:
        return False
    if args.max_book_age_seconds is None:
        return True
    order_book = raw.get("order_book")
    if not order_book or not order_book.get("timestamp"):
        return False
    timestamp = datetime.fromisoformat(raw["timestamp"])
    book_timestamp = datetime.fromisoformat(order_book["timestamp"])
    age_seconds = Decimal(str(max(0.0, (timestamp - book_timestamp).total_seconds())))
    return age_seconds <= args.max_book_age_seconds


def sample_one_per_symbol(candidates: list[Candidate], rng: random.Random) -> list[Candidate]:
    by_symbol: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        by_symbol.setdefault(candidate.symbol, []).append(candidate)
    return [rng.choice(items) for items in by_symbol.values()]


def write_signals(
    candidates: list[Candidate],
    output: Path,
    target_r_multiple: Decimal,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as f:
        for candidate in candidates:
            risk = candidate.price - candidate.vwap
            target = candidate.price + (risk * target_r_multiple)
            signal = StrategySignal(
                source=SignalSource.RULE,
                symbol=candidate.symbol,
                price=candidate.price,
                action=Action.BUY,
                confidence=0.6,
                reasoning=(
                    "random_entry_baseline "
                    f"seeded_sample stop={candidate.vwap} risk_bps={candidate.risk_bps:.3f}"
                ),
                stop_loss_price=candidate.vwap,
                target_price=target,
                trailing_stop_pct=None,
                max_hold_days=None,
                **execution_fields(candidate.raw),
                created_at=candidate.timestamp,
            )
            f.write(signal.model_dump_json())
            f.write("\n")


def execution_fields(raw: dict[str, Any]) -> dict[str, Any]:
    return {name: raw.get(name) for name in EXECUTION_FIELD_NAMES}


def to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


if __name__ == "__main__":
    raise SystemExit(main())
