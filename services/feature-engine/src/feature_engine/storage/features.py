from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

from trade_contracts.features import ProcessedFeatures

JST = ZoneInfo("Asia/Tokyo")

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class FeatureArchiveWriter:
    """ProcessedFeatures を JSONL で日付・銘柄ごとに保存する。"""

    base_dir: Path
    flush_threshold: int = 1000
    _buffers: dict[str, list[ProcessedFeatures]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.flush_threshold <= 0:
            raise ValueError(f"flush_threshold must be positive, got {self.flush_threshold}")

    def record_features(self, features: ProcessedFeatures) -> list[Path]:
        if features.timestamp.tzinfo is None:
            raise ValueError(f"features.timestamp must be tz-aware: {features.timestamp!r}")
        buf = self._buffers.setdefault(features.symbol, [])
        buf.append(features)
        if len(buf) >= self.flush_threshold:
            return self.flush(symbol=features.symbol)
        return []

    def flush(self, *, symbol: str | None = None) -> list[Path]:
        targets: Iterable[str] = [symbol] if symbol is not None else list(self._buffers.keys())
        written: list[Path] = []
        for sym in targets:
            buf = self._buffers.get(sym)
            if not buf:
                continue
            by_date: dict[date, list[ProcessedFeatures]] = {}
            for features in sorted(buf, key=lambda item: item.timestamp):
                d = features.timestamp.astimezone(JST).date()
                by_date.setdefault(d, []).append(features)
            for d, rows in by_date.items():
                written.append(self._write_partition(sym, d, rows))
            self._buffers[sym] = []
        return written

    def _write_partition(self, symbol: str, d: date, rows: list[ProcessedFeatures]) -> Path:
        part_dir = self.base_dir / f"symbol={symbol}" / f"date={d.isoformat()}"
        part_dir.mkdir(parents=True, exist_ok=True)
        first_ms = int(rows[0].timestamp.timestamp() * 1000)
        last_ms = int(rows[-1].timestamp.timestamp() * 1000)
        path = part_dir / f"features_{first_ms}_{last_ms}.jsonl"
        with path.open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(row.model_dump_json())
                f.write("\n")
        logger.info("feature archive written: path=%s rows=%d", path, len(rows))
        return path


def enumerate_feature_symbols(feature_dir: Path, d: date) -> list[str]:
    if not feature_dir.exists():
        return []
    target_date = d.isoformat()
    symbols: list[str] = []
    for sym_dir in feature_dir.glob("symbol=*"):
        if not sym_dir.is_dir():
            continue
        date_dir = sym_dir / f"date={target_date}"
        if not date_dir.is_dir() or not any(date_dir.glob("*.jsonl")):
            continue
        symbols.append(sym_dir.name.removeprefix("symbol="))
    return sorted(symbols)
