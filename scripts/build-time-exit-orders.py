#!/usr/bin/env python3
"""Build diagnostic time-exit order stream from BUY OrderRequest JSONL."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--exit-minutes", type=int, required=True)
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    buy_count = 0
    sell_count = 0
    rows: list[dict[str, object]] = []
    with args.input.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            raw = json.loads(line)
            if raw.get("side") != "BUY":
                continue
            raw["stop_loss_price"] = None
            raw["target_price"] = None
            raw["trailing_stop_pct"] = None
            rows.append(raw)
            buy_count += 1

            created_at = datetime.fromisoformat(str(raw["created_at"]))
            rows.append(
                {
                    "order_id": str(uuid4()),
                    "unified_signal_id": raw.get("unified_signal_id"),
                    "symbol": raw["symbol"],
                    "side": "SELL",
                    "quantity": raw["quantity"],
                    "order_type": "MARKET",
                    "limit_price": None,
                    "trade_mode": raw["trade_mode"],
                    "signal_source": raw["signal_source"],
                    "stop_loss_price": None,
                    "target_price": None,
                    "trailing_stop_pct": None,
                    "max_hold_days": None,
                    "created_at": (created_at + timedelta(minutes=args.exit_minutes)).isoformat(),
                }
            )
            sell_count += 1

    rows.sort(key=lambda item: str(item["created_at"]))
    with args.output.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
            f.write("\n")

    print(
        f"buy_orders={buy_count} sell_orders={sell_count} "
        f"exit_minutes={args.exit_minutes} output={args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
