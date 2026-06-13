from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

from trade_contracts.order import OrderRequest

logger = logging.getLogger(__name__)
JST = ZoneInfo("Asia/Tokyo")


@dataclass(slots=True)
class OrderArchiveWriter:
    """Approved OrderRequest を OMS backtest 再現用 JSONL として追記保存する。"""

    base_dir: Path
    timezone: ZoneInfo = JST

    def record_order(self, order: OrderRequest) -> Path:
        d = order.created_at.astimezone(self.timezone).date()
        path = self.path_for(order.trade_mode.value, d)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(order.model_dump_json())
            f.write("\n")
        logger.info(
            "order archived: path=%s symbol=%s order_id=%s", path, order.symbol, order.order_id
        )
        return path

    def path_for(self, trade_mode: str, d: date) -> Path:
        return self.base_dir / f"trade_mode={trade_mode}" / f"date={d.isoformat()}" / "orders.jsonl"
