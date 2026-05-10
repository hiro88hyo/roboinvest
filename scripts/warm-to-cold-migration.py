#!/usr/bin/env python3
"""Warm Parquet → Cold OHLCV (1m / 5m) 日次バッチ。

`feature_engine.storage.cold.migrate_warm_to_cold` を CLI でラップする薄い
ワークスペース内スクリプト。

使い方 (workspace ルートから):
  uv run python scripts/warm-to-cold-migration.py --date 2026-04-20 --resolution 1m
  uv run python scripts/warm-to-cold-migration.py --date 2026-04-20 --resolution 5m \\
      --symbols 7203,9432 --warm-dir ./data/warm --cold-dir ./data/cold
  uv run python scripts/warm-to-cold-migration.py --date 2026-04-20 --resolution 1m \\
      --delete-warm   # 書き出し成功した symbol/date のみ Warm 側を削除

exit code: 0 = 正常終了 (件数 0 でも 0) / 2 = 引数不正 or warm-dir 不在 or env 未設定。
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date
from pathlib import Path
from typing import Literal, cast, get_args

from feature_engine.storage.cold import (
    ColdResolution,
    delete_warm_partition,
    enumerate_warm_symbols,
    migrate_warm_to_cold,
)

_RESOLUTIONS: tuple[str, ...] = get_args(ColdResolution)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--date", required=True, help="対象日 YYYY-MM-DD")
    p.add_argument(
        "--resolution",
        required=True,
        choices=_RESOLUTIONS,
        help="Cold OHLCV の粒度",
    )
    p.add_argument(
        "--symbols",
        default=None,
        help="カンマ区切りの対象銘柄 (省略時は warm-dir 配下を glob)",
    )
    p.add_argument(
        "--warm-dir",
        default=None,
        help="Warm Parquet 配置先。省略時は env STORAGE_WARM_DIR",
    )
    p.add_argument(
        "--cold-dir",
        default=None,
        help="Cold Parquet 配置先。省略時は env STORAGE_COLD_DIR",
    )
    p.add_argument(
        "--delete-warm",
        action="store_true",
        help=(
            "書き出し成功した symbol/date の Warm Parquet を削除する。"
            "デフォルトは無効 (運用安全のため明示 opt-in)。"
        ),
    )
    p.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="ログレベル (default: INFO)",
    )
    return p.parse_args()


def _resolve_dir(arg_value: str | None, env_name: str, label: str) -> Path:
    raw = arg_value if arg_value is not None else os.environ.get(env_name)
    if not raw:
        print(
            f"NG  --{label}-dir or env {env_name} required",
            file=sys.stderr,
            flush=True,
        )
        sys.exit(2)
    return Path(raw).expanduser()


def _parse_date(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        print(f"NG  invalid --date: {raw} ({exc})", file=sys.stderr, flush=True)
        sys.exit(2)


def _parse_symbols(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    syms = [s.strip() for s in raw.split(",") if s.strip()]
    if not syms:
        print("NG  --symbols is empty", file=sys.stderr, flush=True)
        sys.exit(2)
    return syms


def run(args: argparse.Namespace) -> int:
    target_date = _parse_date(args.date)
    resolution = cast(ColdResolution, args.resolution)
    warm_dir = _resolve_dir(args.warm_dir, "STORAGE_WARM_DIR", "warm")
    cold_dir = _resolve_dir(args.cold_dir, "STORAGE_COLD_DIR", "cold")
    delete_after = cast(bool, args.delete_warm)

    if not warm_dir.exists():
        print(f"NG  warm-dir does not exist: {warm_dir}", file=sys.stderr, flush=True)
        return 2

    explicit_symbols = _parse_symbols(args.symbols)
    if explicit_symbols is not None:
        symbols = explicit_symbols
        symbol_source: Literal["arg", "glob"] = "arg"
    else:
        symbols = enumerate_warm_symbols(warm_dir, target_date)
        symbol_source = "glob"

    print(f"date        : {target_date.isoformat()}", flush=True)
    print(f"resolution  : {resolution}", flush=True)
    print(f"warm-dir    : {warm_dir}", flush=True)
    print(f"cold-dir    : {cold_dir}", flush=True)
    print(f"symbols     : {len(symbols)} ({symbol_source})", flush=True)
    print(f"delete-warm : {delete_after}", flush=True)
    print("", flush=True)

    if not symbols:
        print("no symbols to migrate (empty warm partition)", flush=True)
        return 0

    written: list[str] = []
    skipped: list[str] = []
    deleted_total = 0

    for symbol in symbols:
        out = migrate_warm_to_cold(warm_dir, cold_dir, symbol, target_date, resolution)
        if out is None:
            print(f"  - {symbol} skipped (empty warm partition)", flush=True)
            skipped.append(symbol)
            continue
        print(f"  + {symbol} written {out}", flush=True)
        written.append(symbol)
        if delete_after:
            removed = delete_warm_partition(warm_dir, symbol, target_date)
            deleted_total += removed

    print("", flush=True)
    print(f"summary: written={len(written)} skipped={len(skipped)}", flush=True)
    if delete_after:
        print(f"deleted warm files: {deleted_total}", flush=True)
    return 0


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    sys.exit(run(args))


if __name__ == "__main__":
    main()
