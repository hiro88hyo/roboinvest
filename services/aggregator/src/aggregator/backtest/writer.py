from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

from trade_contracts.signal import UnifiedTradeSignal

logger = logging.getLogger(__name__)


def write_jsonl(signals: Iterable[UnifiedTradeSignal], path: Path) -> int:
    """Write `UnifiedTradeSignal` records as JSONL. Existing file is overwritten."""
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as f:
        for signal in signals:
            f.write(signal.model_dump_json())
            f.write("\n")
            count += 1
    logger.info("wrote jsonl: path=%s rows=%d", path, count)
    return count
