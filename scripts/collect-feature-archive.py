#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from trade_contracts.features import ProcessedFeatures


def _parse_symbols(raw: str | None) -> set[str] | None:
    if raw is None:
        return None
    symbols = {item.strip() for item in raw.split(",") if item.strip()}
    return symbols or None


def _iter_archive_files(
    features_dir: Path,
    target_date: date,
    symbols: set[str] | None,
) -> list[Path]:
    files: list[Path] = []
    date_part = f"date={target_date.isoformat()}"
    for symbol_dir in sorted(features_dir.glob("symbol=*")):
        if not symbol_dir.is_dir():
            continue
        symbol = symbol_dir.name.removeprefix("symbol=")
        if symbols is not None and symbol not in symbols:
            continue
        date_dir = symbol_dir / date_part
        if date_dir.is_dir():
            files.extend(sorted(date_dir.glob("*.jsonl")))
    return files


def collect_features(
    *,
    features_dir: Path,
    target_date: date,
    output: Path,
    symbols: set[str] | None = None,
) -> int:
    rows: list[ProcessedFeatures] = []
    for path in _iter_archive_files(features_dir, target_date, symbols):
        with path.open(encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(ProcessedFeatures.model_validate_json(line))
                except Exception as exc:
                    msg = f"invalid ProcessedFeatures JSON: path={path} line={line_no}"
                    raise ValueError(msg) from exc

    rows.sort(key=lambda item: (item.timestamp, item.symbol))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(row.model_dump_json())
            f.write("\n")
    return len(rows)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect archived ProcessedFeatures partitions into one sorted JSONL file.",
    )
    parser.add_argument("--date", required=True, type=date.fromisoformat, help="Trading date.")
    parser.add_argument(
        "--features-dir",
        required=True,
        type=Path,
        help="Feature archive root, usually OUT/features.",
    )
    parser.add_argument("--output", required=True, type=Path, help="Output JSONL path.")
    parser.add_argument("--symbols", default=None, help="Optional comma-separated symbols.")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    count = collect_features(
        features_dir=args.features_dir,
        target_date=args.date,
        output=args.output,
        symbols=_parse_symbols(args.symbols),
    )
    print(f"collected features: rows={count} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
