from __future__ import annotations

import json
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
from aggregator.clients.supabase import SupabaseError, SupabaseWriter
from trade_contracts.enums import Action, SignalSource, TradeMode, TradingStyle
from trade_contracts.event_paper_dispatch import (
    EVENT_PAPER_EXECUTION_STRATEGY_KEY,
    EventPaperDispatchStage,
)
from trade_contracts.signal import UnifiedTradeSignal

Handler = Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]


def _unified(
    *,
    signal_id: str = "11111111-1111-1111-1111-111111111111",
    symbol: str = "7203",
    action: Action = Action.BUY,
    confidence: float = 0.8,
    signal_source: SignalSource = SignalSource.CONSENSUS,
    strategy_signal_id_a: str | None = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    strategy_signal_id_b: str | None = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
) -> UnifiedTradeSignal:
    return UnifiedTradeSignal(
        signal_id=UUID(signal_id),
        symbol=symbol,
        action=action,
        confidence=confidence,
        signal_source=signal_source,
        strategy_signal_id_a=UUID(strategy_signal_id_a) if strategy_signal_id_a else None,
        strategy_signal_id_b=UUID(strategy_signal_id_b) if strategy_signal_id_b else None,
        holding_type=TradingStyle.DAY,
        created_at=datetime(2026, 4, 20, 9, 0, tzinfo=UTC),
    )


async def test_insert_aggregator_logs_posts_rows_and_returns_count() -> None:
    captured: list[httpx.Request] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(201, content=b"")

    async with SupabaseWriter(
        url="https://example.supabase.co",
        secret_key="k",
        transport=httpx.MockTransport(_handler),
    ) as writer:
        n = await writer.insert_aggregator_logs(
            [
                _unified(symbol="7203"),
                _unified(
                    signal_id="22222222-2222-2222-2222-222222222222",
                    symbol="9984",
                    action=Action.SELL,
                    signal_source=SignalSource.RULE,
                    strategy_signal_id_b=None,
                ),
            ]
        )

    assert n == 2
    assert len(captured) == 1
    req = captured[0]
    assert req.url.path == "/rest/v1/aggregator_logs"
    assert req.url.params.get("on_conflict") == "signal_id"
    assert req.headers.get("Prefer") == "resolution=merge-duplicates,return=minimal"
    body = json.loads(req.content.decode())
    assert body[0]["signal_id"] == "11111111-1111-1111-1111-111111111111"
    assert body[0]["signal_source"] == "CONSENSUS"
    assert body[0]["symbol"] == "7203"
    assert body[0]["action"] == "BUY"
    assert body[0]["strategy_signal_id_a"] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert body[0]["strategy_signal_id_b"] == "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    assert body[0]["created_at"].startswith("2026-04-20T09:00")
    assert body[1]["signal_source"] == "RULE"
    assert body[1]["strategy_signal_id_b"] is None


async def test_insert_aggregator_logs_skips_request_on_empty_input() -> None:
    called = 0

    async def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called += 1
        return httpx.Response(500)

    async with SupabaseWriter(
        url="https://example.supabase.co",
        secret_key="k",
        transport=httpx.MockTransport(_handler),
    ) as writer:
        n = await writer.insert_aggregator_logs([])

    assert n == 0
    assert called == 0


async def test_insert_aggregator_logs_raises_on_4xx() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="nope")

    async with SupabaseWriter(
        url="https://example.supabase.co",
        secret_key="k",
        transport=httpx.MockTransport(_handler),
    ) as writer:
        with pytest.raises(SupabaseError):
            await writer.insert_aggregator_logs([_unified()])


async def test_read_trade_mode_reads_system_status() -> None:
    captured: list[httpx.Request] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=[{"trade_mode": "paper"}])

    async with SupabaseWriter(
        url="https://example.supabase.co",
        secret_key="k",
        transport=httpx.MockTransport(_handler),
    ) as writer:
        mode = await writer.read_trade_mode()

    assert mode is TradeMode.PAPER
    assert captured[0].url.path == "/rest/v1/system_status"
    assert captured[0].url.params.get("id") == "eq.1"


async def test_read_long_quantity_sums_position_rows() -> None:
    captured: list[httpx.Request] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=[{"quantity": 100}, {"quantity": 200}])

    async with SupabaseWriter(
        url="https://example.supabase.co",
        secret_key="k",
        transport=httpx.MockTransport(_handler),
    ) as writer:
        qty = await writer.read_long_quantity(symbol="7203", trade_mode=TradeMode.PAPER)

    assert qty == 300
    req = captured[0]
    assert req.url.path == "/rest/v1/positions"
    assert req.url.params.get("symbol") == "eq.7203"
    assert req.url.params.get("trade_type") == "eq.paper"
    assert req.url.params.get("side") == "eq.LONG"


async def test_event_paper_dispatch_client_uses_rpc_and_parses_confirmed_checkpoint() -> None:
    signal_id = uuid4()
    input_payload = {
        "routing_intent": "PAPER_ONLY",
        "strategy_key": EVENT_PAPER_EXECUTION_STRATEGY_KEY,
        "symbol": "7203",
    }
    calls: list[dict[str, object]] = []
    state = {
        "stage": "aggregator",
        "input_signal_id": str(signal_id),
        "input_payload": input_payload,
        "input_payload_sha256": "a" * 64,
        "output_payload": input_payload,
        "output_payload_sha256": "b" * 64,
        "destination_topic": "trade-signals",
        "status": "prepared",
        "attempt_id": None,
        "attempted_at": None,
        "pubsub_message_id": None,
        "confirmed_at": None,
        "last_error": None,
    }

    async def _handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/v1/rpc/event_paper_stage_dispatch"
        payload = json.loads(request.content.decode())
        calls.append(payload)
        action = payload["p_action"]
        if action == "prepare":
            state.update(
                input_payload_sha256=payload["p_input_payload_sha256"],
                output_payload_sha256=payload["p_output_payload_sha256"],
            )
            outcome = "prepared"
        elif action == "begin":
            state.update(
                status="attempting",
                attempt_id=payload["p_attempt_id"],
                attempted_at=payload["p_occurred_at"],
            )
            outcome = "attempt_started"
        elif action == "confirm":
            state.update(
                status="confirmed",
                pubsub_message_id=payload["p_pubsub_message_id"],
                confirmed_at=payload["p_occurred_at"],
            )
            outcome = "confirmed"
        else:
            raise AssertionError(f"unexpected action: {action}")
        return httpx.Response(200, json=[{"outcome": outcome, **state}])

    now = datetime(2026, 4, 20, 9, 0, tzinfo=UTC)
    async with SupabaseWriter(
        url="https://example.supabase.co",
        secret_key="k",
        transport=httpx.MockTransport(_handler),
    ) as writer:
        prepared = await writer.prepare_event_paper_dispatch(
            stage=EventPaperDispatchStage.AGGREGATOR,
            input_signal_id=signal_id,
            input_payload=input_payload,
            output_payload=input_payload,
            destination_topic="trade-signals",
        )
        begun = await writer.begin_event_paper_dispatch(
            stage=EventPaperDispatchStage.AGGREGATOR,
            input_signal_id=signal_id,
            attempt_id="attempt-1",
            attempted_at=now,
        )
        confirmed = await writer.confirm_event_paper_dispatch(
            stage=EventPaperDispatchStage.AGGREGATOR,
            input_signal_id=signal_id,
            attempt_id="attempt-1",
            pubsub_message_id="message-1",
            confirmed_at=now,
        )

    assert prepared.outcome.value == "prepared"
    assert begun.outcome.value == "attempt_started"
    assert confirmed.outcome.value == "confirmed"
    assert confirmed.confirmed_at == now
    assert [call["p_action"] for call in calls] == ["prepare", "begin", "confirm"]
