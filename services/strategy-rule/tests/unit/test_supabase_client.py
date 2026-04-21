from __future__ import annotations

import json
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import httpx
import pytest
from strategy_rule.clients.supabase import SupabaseError, SupabaseWriter
from trade_contracts.enums import Side, SignalSource
from trade_contracts.signal import StrategySignal

Handler = Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]


def _signal(
    *,
    signal_id: str = "11111111-1111-1111-1111-111111111111",
    symbol: str = "7203",
    action: Side = Side.BUY,
    confidence: Decimal = Decimal("0.8"),
    reasoning: str | None = None,
) -> StrategySignal:
    return StrategySignal(
        signal_id=UUID(signal_id),
        source=SignalSource.RULE,
        symbol=symbol,
        action=action,
        confidence=confidence,
        reasoning=reasoning,
        created_at=datetime(2026, 4, 20, 9, 0, tzinfo=UTC),
    )


async def test_insert_strategy_logs_posts_rows_and_returns_count() -> None:
    captured: list[httpx.Request] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(201, content=b"")

    async with SupabaseWriter(
        url="https://example.supabase.co",
        secret_key="k",
        transport=httpx.MockTransport(_handler),
    ) as writer:
        n = await writer.insert_strategy_logs(
            [
                _signal(symbol="7203", action=Side.BUY),
                _signal(
                    signal_id="22222222-2222-2222-2222-222222222222",
                    symbol="9984",
                    action=Side.SELL,
                    reasoning="threshold breached",
                ),
            ]
        )

    assert n == 2
    assert len(captured) == 1
    req = captured[0]
    assert req.url.path == "/rest/v1/strategy_logs"
    assert req.url.params.get("on_conflict") == "signal_id"
    assert req.headers.get("Prefer") == "resolution=merge-duplicates,return=minimal"
    body = json.loads(req.content.decode())
    assert body[0]["signal_id"] == "11111111-1111-1111-1111-111111111111"
    assert body[0]["source"] == "RULE"
    assert body[0]["symbol"] == "7203"
    assert body[0]["action"] == "BUY"
    assert body[0]["created_at"].startswith("2026-04-20T09:00")
    assert body[1]["reasoning"] == "threshold breached"


async def test_insert_strategy_logs_skips_request_on_empty_input() -> None:
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
        n = await writer.insert_strategy_logs([])

    assert n == 0
    assert called == 0


async def test_insert_strategy_logs_raises_on_4xx() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="nope")

    async with SupabaseWriter(
        url="https://example.supabase.co",
        secret_key="k",
        transport=httpx.MockTransport(_handler),
    ) as writer:
        with pytest.raises(SupabaseError):
            await writer.insert_strategy_logs([_signal()])
