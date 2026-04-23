"""Phase 1 ルーティング(純関数)。

``trade_mode`` に応じて配信先 Pub/Sub トピック名を返す。
"""

from __future__ import annotations

from dataclasses import dataclass

from trade_contracts.enums import TradeMode


@dataclass(frozen=True, slots=True)
class TopicRouting:
    live_topic: str
    paper_topic: str


def resolve_topic(trade_mode: TradeMode, routing: TopicRouting) -> str:
    if trade_mode is TradeMode.LIVE:
        return routing.live_topic
    if trade_mode is TradeMode.PAPER:
        return routing.paper_topic
    raise AssertionError(f"unexpected trade_mode: {trade_mode}")
