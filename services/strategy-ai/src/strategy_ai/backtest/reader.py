from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

from trade_contracts.features import ProcessedFeatures

logger = logging.getLogger(__name__)


def iter_features(path: Path) -> Iterator[ProcessedFeatures]:
    """Stream `ProcessedFeatures` from a JSONL file.

    Blank lines are skipped. Malformed rows raise `pydantic.ValidationError` —
    callers decide whether to abort or skip.
    """
    with path.open("r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            yield ProcessedFeatures.model_validate_json(line)
