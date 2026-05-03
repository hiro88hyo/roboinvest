#!/usr/bin/env python3
"""scripts/replay-market-data.py — feature-engine backtest の薄いラッパー。

Supabase `daily_ohlcv` を入力に過去日のリプレイ (バックテスト) を実行し、
`ProcessedFeatures` を JSONL/Parquet で出力する。処理本体は
`services/feature-engine/src/feature_engine/__main__.py` の `backtest`
サブコマンドに委譲し、本スクリプトは `backtest` サブコマンドを自動付与する
だけのワンステップエントリポイント。

使い方:
    uv run python scripts/replay-market-data.py --date 2025-01-15
    uv run python scripts/replay-market-data.py --date 2025-01-15 --symbols 7203,9984 --output both

引数は feature-engine CLI と同一 (`--date`, `--symbols`, `--output`,
`--output-dir`)。詳細は `uv run python -m feature_engine backtest --help` を参照。
"""

from __future__ import annotations

import sys

from feature_engine.__main__ import main as feature_engine_main


def main() -> None:
    sys.argv = [sys.argv[0], "backtest", *sys.argv[1:]]
    feature_engine_main()


if __name__ == "__main__":
    main()
