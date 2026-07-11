"""Pure fresh-book validation and StrategySignal construction."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from trade_contracts.enums import Action, SignalSource
from trade_contracts.market import OrderBookSnapshot
from trade_contracts.signal import StrategySignal, deterministic_strategy_signal_id
from trade_contracts.tick_size import tse_tick_size

from .artifact import EventPaperCandidate
from .models import (
    EVENT_EXECUTION_STRATEGY_KEY,
    EventPaperPublishConfig,
    EventPaperSignalClaim,
    EventPaperSignalFields,
    claim_json,
    parse_claim_json,
)

JST = ZoneInfo("Asia/Tokyo")


def entry_window_rejection(
    *,
    now: datetime,
    target_date: object,
    config: EventPaperPublishConfig,
) -> str | None:
    if now.tzinfo is None:
        return "naive_wall_clock"
    local = now.astimezone(JST)
    if local.date() != target_date:
        return "wrong_entry_date"
    if local.time().replace(tzinfo=None) < config.entry_window_start:
        return "before_entry_window"
    if local.time().replace(tzinfo=None) >= config.entry_window_end:
        return "after_entry_window"
    return None


def book_rejection_reason(
    *,
    book: OrderBookSnapshot,
    candidate: EventPaperCandidate,
    now: datetime,
    config: EventPaperPublishConfig,
) -> str | None:
    if book.symbol != candidate.symbol:
        return "symbol_mismatch"
    received_at = book.received_at
    if received_at is None:
        return "missing_received_at"
    if received_at.tzinfo is None:
        return "naive_received_at"
    if received_at.astimezone(JST).date() != candidate.entry_date:
        return "wrong_book_date"
    received_local_time = received_at.astimezone(JST).time().replace(tzinfo=None)
    if received_local_time < config.entry_window_start:
        return "book_before_entry_window"
    if received_local_time >= config.entry_window_end:
        return "book_after_entry_window"
    if now.tzinfo is None:
        return "naive_wall_clock"
    try:
        age_seconds = (now - received_at).total_seconds()
    except TypeError:
        return "invalid_received_at"
    if age_seconds < -config.max_future_skew_seconds:
        return "future_book"
    if age_seconds > config.max_book_age_seconds:
        return "stale_book"
    if not book.bids:
        return "missing_bid"
    if not book.asks:
        return "missing_ask"
    if book.bids[0].quantity <= 0 or book.asks[0].quantity <= 0:
        return "empty_top_of_book"
    if any(level.price <= 0 or level.quantity < 0 for level in (*book.bids, *book.asks)):
        return "invalid_book_level"
    if book.bids[0].price != max(level.price for level in book.bids):
        return "unsorted_bids"
    if book.asks[0].price != min(level.price for level in book.asks):
        return "unsorted_asks"
    if book.bids[0].price >= book.asks[0].price:
        return "crossed_or_locked_book"
    if sum(level.quantity for level in book.bids[:5]) <= 0:
        return "missing_bid_depth"
    if sum(level.quantity for level in book.asks[:5]) <= 0:
        return "missing_ask_depth"
    return None


def build_signal_claim(
    *,
    candidate: EventPaperCandidate,
    book: OrderBookSnapshot,
    raw_book_message_id: str,
    artifact_sha256: str,
    config: EventPaperPublishConfig,
) -> tuple[EventPaperSignalClaim, StrategySignal]:
    if book.received_at is None:
        raise ValueError("book received_at is required")
    best_bid = book.bids[0].price
    best_ask = book.asks[0].price
    spread = best_ask - best_bid
    mid = (best_bid + best_ask) / Decimal("2")
    tick_size = tse_tick_size(mid)
    bid_depth_1 = book.bids[0].quantity
    ask_depth_1 = book.asks[0].quantity
    bid_depth_5 = sum(level.quantity for level in book.bids[:5])
    ask_depth_5 = sum(level.quantity for level in book.asks[:5])
    fields = EventPaperSignalFields(
        candidate_id=candidate.execution_candidate_id,
        symbol=candidate.symbol,
        price=best_ask,
        confidence=config.confidence,
        best_bid=best_bid,
        best_ask=best_ask,
        spread_bps=(spread / mid) * Decimal("10000"),
        tick_size=tick_size,
        spread_ticks=spread / tick_size,
        bid_depth_1=bid_depth_1,
        ask_depth_1=ask_depth_1,
        bid_depth_5=bid_depth_5,
        ask_depth_5=ask_depth_5,
        book_imbalance_5=(Decimal(bid_depth_5 - ask_depth_5) / Decimal(bid_depth_5 + ask_depth_5)),
        created_at=book.received_at,
    )
    claim = EventPaperSignalClaim(
        artifact_sha256=artifact_sha256,
        raw_book_message_id=raw_book_message_id,
        raw_book_received_at=book.received_at,
        cluster_id=candidate.cluster_id,
        observation_id=candidate.observation_id,
        event_ids=candidate.event_ids,
        signal_date=candidate.signal_date,
        entry_date=candidate.entry_date,
        signal_fields=fields,
    )
    signal = StrategySignal.model_validate(
        {
            **fields.model_dump(mode="json"),
            "reasoning": claim_json(claim),
        }
    )
    return claim, signal


def signal_from_claim(
    reasoning: str | None,
    *,
    candidate: EventPaperCandidate,
    artifact_sha256: str,
) -> tuple[EventPaperSignalClaim, StrategySignal]:
    claim = parse_claim_json(reasoning)
    if claim.artifact_sha256 != artifact_sha256:
        raise ValueError("existing signal claim belongs to a different artifact")
    if claim.cluster_id != candidate.cluster_id or claim.observation_id != candidate.observation_id:
        raise ValueError("existing signal claim belongs to a different occurrence")
    if claim.entry_date != candidate.entry_date or claim.signal_date != candidate.signal_date:
        raise ValueError("existing signal claim dates do not match the candidate")
    if claim.event_ids != candidate.event_ids:
        raise ValueError("existing signal claim event lineage does not match the candidate")
    if claim.signal_fields.symbol != candidate.symbol:
        raise ValueError("existing signal claim symbol does not match the candidate")
    signal = StrategySignal.model_validate(
        {
            **claim.signal_fields.model_dump(mode="json"),
            "reasoning": claim_json(claim),
        }
    )
    expected_signal_id = deterministic_strategy_signal_id(
        strategy_key=EVENT_EXECUTION_STRATEGY_KEY,
        candidate_id=candidate.execution_candidate_id,
        source=SignalSource.RULE,
        symbol=candidate.symbol,
        action=Action.BUY,
    )
    if signal.signal_id != expected_signal_id:
        raise ValueError("existing signal claim deterministic signal ID mismatch")
    return claim, signal
