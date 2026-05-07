#!/usr/bin/env python3
"""kabu ``/positions`` と Supabase ``positions(live)`` の整合性チェック CLI。

設計指針は ``services/oms-live/src/oms_live/reconciler.py`` の docstring と
memory ``positions_integrity.md`` を参照。

3 種類の差分を出力する:

  1. **kabu only** — kabu に建玉があるが Supabase に行が無い。
     ``--apply`` 指定時のみ Supabase ``positions(live)`` に取り込む
     (``holding_type=swing`` / ``opened_at=now`` / 各種閾値 ``None``)
  2. **quantity mismatch** — 両方にあるが数量が違う。**warning のみ**。
     強制同期は禁止 (個人保有を巻き込む事故防止)
  3. **supabase only** — Supabase にあって kabu に無い行。**warning のみ**。
     強制 DELETE はしない

env (必須):
  KABU_API_BASE_URL    例: http://192.168.2.21:28080/kabusapi
  KABU_API_PASSWORD    kabu API パスワード
  SUPABASE_URL         例: http://localhost:54321
  SUPABASE_SECRET_KEY  service_role / sb_secret_*

使い方 (workspace ルートから):
  KABU_API_BASE_URL=... KABU_API_PASSWORD=... \\
  SUPABASE_URL=... SUPABASE_SECRET_KEY=... \\
      uv run python scripts/reconcile-positions.py            # dry-run

  ... uv run python scripts/reconcile-positions.py --apply    # 取り込み実行

OMS Live と並行起動しないこと (write path 競合を避ける)。kabu ``/positions`` は GET
なので市場時間外でも叩ける。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import UTC, datetime

from oms_live.clients.supabase import SupabaseClient
from oms_live.kabu_client import KabuLiveClient
from oms_live.reconciler import (
    apply_reconcile_actions,
    compute_position_diff,
    parse_kabu_positions,
)
from trade_contracts.enums import TradingStyle

logger = logging.getLogger("reconcile-positions")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help=(
            "to_import (kabu only) を Supabase に INSERT する。"
            "デフォルトは dry-run (warning ログのみ)。"
            "quantity_mismatch / supabase_orphans は --apply 指定時も触らない。"
        ),
    )
    p.add_argument(
        "--product",
        type=int,
        default=1,
        help="kabu /positions の product フィルタ (1=現物, 0=すべて, default: 1)",
    )
    p.add_argument(
        "--holding-type",
        choices=["day", "swing"],
        default="swing",
        help=(
            "取り込み時に LivePosition.holding_type に設定する値 (default: swing)。"
            "day にすると 14:50 closeout の対象になる点に注意"
        ),
    )
    p.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="ログレベル (default: INFO)",
    )
    return p.parse_args()


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"NG  required env not set: {name}", file=sys.stderr, flush=True)
        sys.exit(2)
    return value


async def _run(args: argparse.Namespace) -> int:
    base_url = _require_env("KABU_API_BASE_URL")
    api_password = _require_env("KABU_API_PASSWORD")
    supabase_url = _require_env("SUPABASE_URL")
    supabase_key = _require_env("SUPABASE_SECRET_KEY")

    holding_type = TradingStyle(args.holding_type)
    now = datetime.now(UTC)

    print(f"target kabu : {base_url}", flush=True)
    print(f"target supab: {supabase_url}", flush=True)
    print(f"mode        : {'APPLY' if args.apply else 'dry-run'}", flush=True)
    print(f"holding_type: {holding_type.value}", flush=True)
    print("", flush=True)

    async with (
        KabuLiveClient(base_url=base_url, api_password=api_password) as kabu,
        SupabaseClient(url=supabase_url, secret_key=supabase_key) as supabase,
    ):
        await kabu.fetch_token()
        kabu_rows = await kabu.list_positions(product=args.product)
        kabu_positions = parse_kabu_positions(kabu_rows)
        supabase_positions = await supabase.list_live_positions()

        print(
            f"kabu positions   : {len(kabu_rows)} raw → {len(kabu_positions)} parsed (LONG only)",
            flush=True,
        )
        print(f"supabase(live)   : {len(supabase_positions)}", flush=True)

        actions = compute_position_diff(kabu_positions, supabase_positions)

        print("", flush=True)
        print(f"matched          : {len(actions.matched)}", flush=True)
        for symbol in actions.matched:
            print(f"  = {symbol}", flush=True)

        print(f"to_import        : {len(actions.to_import)} (kabu only)", flush=True)
        for snap in actions.to_import:
            print(
                f"  + {snap.symbol} qty={snap.quantity} avg={snap.average_price}",
                flush=True,
            )

        print(f"quantity_mismatch: {len(actions.quantity_mismatches)}", flush=True)
        for m in actions.quantity_mismatches:
            print(
                f"  ! {m.symbol} kabu={m.kabu_quantity}@{m.kabu_average_price} "
                f"supabase={m.supabase_quantity}@{m.supabase_entry_price}",
                flush=True,
            )

        print(f"supabase_orphans : {len(actions.supabase_orphans)} (supabase only)", flush=True)
        for orphan in actions.supabase_orphans:
            print(
                f"  ? {orphan.symbol} qty={orphan.quantity} entry={orphan.entry_price}",
                flush=True,
            )

        inserted = await apply_reconcile_actions(
            actions,
            supabase,
            now=now,
            apply_imports=args.apply,
            holding_type=holding_type,
        )

        print("", flush=True)
        if args.apply:
            print(f"applied: imported {inserted} positions", flush=True)
        else:
            print("dry-run: no Supabase write (rerun with --apply to import)", flush=True)

    return 0


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
