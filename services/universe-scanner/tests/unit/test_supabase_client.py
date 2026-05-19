from __future__ import annotations

import json

import httpx
import pytest

from universe_scanner.clients.supabase import SupabaseWriter


@pytest.mark.asyncio
async def test_upsert_splits_rows_into_chunks() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201)

    async with SupabaseWriter(
        url="https://example.supabase.co",
        secret_key="secret",
        upsert_chunk_size=2,
    ) as writer:
        assert writer._client is not None
        await writer._client.aclose()
        writer._client = httpx.AsyncClient(
            base_url="https://example.supabase.co",
            transport=httpx.MockTransport(handler),
        )
        await writer.upsert(
            "daily_ohlcv",
            [
                {"symbol": "7203", "date": "2026-05-19"},
                {"symbol": "9432", "date": "2026-05-19"},
                {"symbol": "9984", "date": "2026-05-19"},
            ],
            on_conflict="symbol,date",
        )

    assert len(requests) == 2
    assert json.loads(requests[0].content.decode()) == [
        {"symbol": "7203", "date": "2026-05-19"},
        {"symbol": "9432", "date": "2026-05-19"},
    ]
    assert json.loads(requests[1].content.decode()) == [
        {"symbol": "9984", "date": "2026-05-19"},
    ]
