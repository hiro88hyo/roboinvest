"""Security and retry-boundary checks for event-paper Supabase RPCs."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from urllib.parse import urlparse
from uuid import uuid4

import httpx
import pytest
from trade_contracts.event_paper_dispatch import (
    EVENT_PAPER_EXECUTION_STRATEGY_KEY,
    canonical_payload_sha256,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def supabase_url() -> str:
    value = os.environ.get("SUPABASE_URL")
    if not value:
        pytest.skip("SUPABASE_URL not set")
    return value


@pytest.fixture
def supabase_anon_key() -> str:
    value = os.environ.get("SUPABASE_ANON_KEY")
    if not value:
        pytest.skip("SUPABASE_ANON_KEY not set")
    return value


@pytest.fixture
def supabase_secret_key() -> str:
    value = os.environ.get("SUPABASE_SECRET_KEY")
    if not value:
        pytest.skip("SUPABASE_SECRET_KEY not set")
    return value


def _headers(key: str) -> dict[str, str]:
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def _require_loopback_supabase(url: str) -> None:
    if urlparse(url).hostname not in {"127.0.0.1", "::1", "localhost"}:
        pytest.skip("mutating event-paper RPC retry test requires loopback Supabase")


async def test_event_paper_cas_rpc_is_not_executable_by_anon(
    supabase_url: str,
    supabase_anon_key: str,
) -> None:
    async with httpx.AsyncClient(base_url=supabase_url.rstrip("/"), timeout=10.0) as client:
        response = await client.post(
            "/rest/v1/rpc/event_paper_cas_strategy_reasoning",
            headers=_headers(supabase_anon_key),
            json={
                "p_signal_id": None,
                "p_expected_reasoning": None,
                "p_updated_reasoning": None,
            },
        )
        dispatch_response = await client.post(
            "/rest/v1/rpc/event_paper_stage_dispatch",
            headers=_headers(supabase_anon_key),
            json={
                "p_action": None,
                "p_stage": None,
                "p_input_signal_id": None,
            },
        )

    assert response.status_code in {401, 403, 404}
    assert dispatch_response.status_code in {401, 403, 404}


async def test_event_paper_dispatch_begin_retries_same_attempt_id(
    supabase_url: str,
    supabase_secret_key: str,
) -> None:
    """A lost begin response must not turn an un-published command ambiguous."""

    _require_loopback_supabase(supabase_url)
    signal_id = uuid4()
    payload = {
        "routing_intent": "PAPER_ONLY",
        "strategy_key": EVENT_PAPER_EXECUTION_STRATEGY_KEY,
    }
    occurred_at = datetime.now(UTC).isoformat()
    attempt_id = f"it-begin-{uuid4().hex}"
    prepare_request = {
        "p_action": "prepare",
        "p_stage": "aggregator",
        "p_input_signal_id": str(signal_id),
        "p_input_payload": payload,
        "p_input_payload_sha256": canonical_payload_sha256(payload),
        "p_output_payload": payload,
        "p_output_payload_sha256": canonical_payload_sha256(payload),
        "p_destination_topic": "trade-signals",
    }
    begin_request = {
        "p_action": "begin",
        "p_stage": "aggregator",
        "p_input_signal_id": str(signal_id),
        "p_attempt_id": attempt_id,
        "p_occurred_at": occurred_at,
    }
    async with httpx.AsyncClient(base_url=supabase_url.rstrip("/"), timeout=10.0) as client:
        prepared = await client.post(
            "/rest/v1/rpc/event_paper_stage_dispatch",
            headers=_headers(supabase_secret_key),
            json=prepare_request,
        )
        first_begin = await client.post(
            "/rest/v1/rpc/event_paper_stage_dispatch",
            headers=_headers(supabase_secret_key),
            json=begin_request,
        )
        retried_begin = await client.post(
            "/rest/v1/rpc/event_paper_stage_dispatch",
            headers=_headers(supabase_secret_key),
            json=begin_request,
        )
        different_attempt = await client.post(
            "/rest/v1/rpc/event_paper_stage_dispatch",
            headers=_headers(supabase_secret_key),
            json={**begin_request, "p_attempt_id": f"it-begin-{uuid4().hex}"},
        )

    for response in (prepared, first_begin, retried_begin, different_attempt):
        response.raise_for_status()
    assert prepared.json()[0]["outcome"] == "prepared"
    assert first_begin.json()[0]["outcome"] == "attempt_started"
    assert retried_begin.json()[0]["outcome"] == "attempt_started"
    assert retried_begin.json()[0]["status"] == "attempting"
    assert different_attempt.json()[0]["outcome"] == "ambiguous"
