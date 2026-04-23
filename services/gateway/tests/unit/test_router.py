from __future__ import annotations

from gateway.router import TopicRouting, resolve_topic
from trade_contracts.enums import TradeMode


def test_live_routes_to_live_topic() -> None:
    routing = TopicRouting(live_topic="live-orders", paper_topic="paper-orders")
    assert resolve_topic(TradeMode.LIVE, routing) == "live-orders"


def test_paper_routes_to_paper_topic() -> None:
    routing = TopicRouting(live_topic="live-orders", paper_topic="paper-orders")
    assert resolve_topic(TradeMode.PAPER, routing) == "paper-orders"


def test_topics_can_be_customized() -> None:
    routing = TopicRouting(live_topic="prod-live", paper_topic="prod-paper")
    assert resolve_topic(TradeMode.LIVE, routing) == "prod-live"
    assert resolve_topic(TradeMode.PAPER, routing) == "prod-paper"
