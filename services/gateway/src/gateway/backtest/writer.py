from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel

logger = logging.getLogger(__name__)


def write_jsonl(records: Iterable[BaseModel], path: Path) -> int:
    """Write pydantic models as JSONL. Existing file is overwritten.

    Any ``BaseModel`` subclass is accepted so the same writer can serialize
    ``OrderRequest`` approved rows and ``RejectionRecord`` rejection rows.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(record.model_dump_json())
            f.write("\n")
            count += 1
    logger.info("wrote jsonl: path=%s rows=%d", path, count)
    return count
