from __future__ import annotations

import base64
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from strategy_rule.clients.pubsub import PubSubPublisher, PubSubSubscriber
from strategy_rule.event_paper._testing import (
    TARGET_DATE,
    make_event_artifact_payload,
    make_event_book,
    make_event_candidate,
)
from strategy_rule.event_paper.artifact import load_event_paper_artifact
from strategy_rule.event_paper.models import (
    EVENT_EXECUTION_PROFILE,
    EVENT_EXECUTION_STRATEGY_KEY,
    EventPaperPublicationAttempt,
    EventPaperPublishConfig,
    EventPaperSignalClaim,
    claim_json,
    parse_claim_json,
)
from strategy_rule.event_paper.publisher import book_rejection_reason, entry_window_rejection
from strategy_rule.event_paper.runner import EventPaperPublishError, EventPaperPublisherRunner
from strategy_rule.event_paper.supabase import EventPaperSupabaseClient
from trade_contracts.enums import Action, RoutingIntent, SignalSource, TradingStyle
from trade_contracts.signal import StrategySignal


@dataclass
class _PubSubRouter:
    pull_batches: list[dict[str, Any]] = field(default_factory=list)
    published: list[httpx.Request] = field(default_factory=list)
    acked: list[str] = field(default_factory=list)
    sought: int = 0
    events: list[str] | None = None
    publish_statuses: list[int] = field(default_factory=list)
    ack_error: Exception | None = None
    on_publish: Callable[[int], None] | None = None
    publish_attempts: int = 0

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(":seek"):
            self.sought += 1
            return httpx.Response(200, json={})
        if request.url.path.endswith(":pull"):
            return httpx.Response(
                200,
                json=self.pull_batches.pop(0) if self.pull_batches else {},
            )
        if request.url.path.endswith(":acknowledge"):
            if self.ack_error is not None:
                raise self.ack_error
            self.acked.extend(json.loads(request.content.decode())["ackIds"])
            return httpx.Response(200, json={})
        if request.url.path.endswith(":publish"):
            self.publish_attempts += 1
            if self.on_publish is not None:
                self.on_publish(self.publish_attempts)
            if self.events is not None:
                self.events.append("publish")
            self.published.append(request)
            status = self.publish_statuses.pop(0) if self.publish_statuses else 200
            if status != 200:
                return httpx.Response(status, text="injected publish failure")
            return httpx.Response(200, json={"messageIds": [f"signal-{len(self.published)}"]})
        return httpx.Response(404, text=f"unmocked {request.method} {request.url.path}")


@dataclass
class _SupabaseRouter:
    trade_mode: str = "paper"
    trade_mode_responses: list[str] = field(default_factory=list)
    allowed: bool = True
    due_positions: list[dict[str, Any]] = field(default_factory=list)
    due_position_responses: list[list[dict[str, Any]]] = field(default_factory=list)
    missing_schedule_positions: list[dict[str, Any]] = field(default_factory=list)
    strategy_logs: dict[str, dict[str, Any]] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)
    events: list[str] | None = None
    on_system_status: Callable[[int], None] | None = None
    on_cas: Callable[[dict[str, Any]], None] | None = None
    system_status_reads: int = 0
    cas_statuses: list[int] = field(default_factory=list)

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.method == "GET" and path == "/rest/v1/system_status":
            self.system_status_reads += 1
            if self.on_system_status is not None:
                self.on_system_status(self.system_status_reads)
            trade_mode = (
                self.trade_mode_responses.pop(0) if self.trade_mode_responses else self.trade_mode
            )
            return httpx.Response(
                200,
                json=[
                    {
                        "trade_mode": trade_mode,
                        "is_trading_allowed": self.allowed,
                    }
                ],
            )
        if request.method == "GET" and path == "/rest/v1/trades_paper":
            return httpx.Response(200, json=[])
        if request.method == "POST" and path.endswith("/rpc/oms_paper_apply_fill"):
            return httpx.Response(400, text="p_order_id and p_trade_id are required")
        if request.method == "POST" and path.endswith("/rpc/oms_paper_update_stop_loss"):
            return httpx.Response(400, text="p_symbol is required")
        if request.method == "POST" and path.endswith("/rpc/event_paper_cas_strategy_reasoning"):
            payload = json.loads(request.content.decode())
            if payload.get("p_signal_id") is None:
                return httpx.Response(400, text="p_signal_id is required")
            status = self.cas_statuses.pop(0) if self.cas_statuses else 200
            if status != 200:
                return httpx.Response(status, text="injected CAS failure")
            signal_id = str(payload["p_signal_id"])
            row = self.strategy_logs.get(signal_id)
            if row is None:
                return httpx.Response(200, json=[{"applied": False, "reasoning": None}])
            if self.on_cas is not None:
                self.on_cas(row)
            applied = row.get("reasoning") == payload["p_expected_reasoning"]
            if applied:
                row["reasoning"] = payload["p_updated_reasoning"]
            return httpx.Response(
                200,
                json=[{"applied": applied, "reasoning": row.get("reasoning")}],
            )
        if request.method == "GET" and path == "/rest/v1/positions":
            if request.url.params.get("scheduled_exit_date") == "is.null":
                return httpx.Response(200, json=self.missing_schedule_positions)
            due_positions = (
                self.due_position_responses.pop(0)
                if self.due_position_responses
                else self.due_positions
            )
            return httpx.Response(200, json=due_positions)
        if request.method == "POST" and path == "/rest/v1/strategy_logs":
            if self.events is not None:
                self.events.append("claim")
            for row in json.loads(request.content.decode()):
                self.strategy_logs.setdefault(str(row["signal_id"]), dict(row))
            return httpx.Response(201, json=[])
        if request.method == "GET" and path == "/rest/v1/strategy_logs":
            signal_id = str(request.url.params["signal_id"]).removeprefix("eq.")
            row = self.strategy_logs.get(signal_id)
            return httpx.Response(200, json=[] if row is None else [row])
        return httpx.Response(404, text=f"unmocked {request.method} {path}")


def _pull_message(
    *,
    ack_id: str,
    message_id: str,
    payload: bytes,
    attributes: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "ackId": ack_id,
        "message": {
            "messageId": message_id,
            "data": base64.b64encode(payload).decode("ascii"),
            "attributes": attributes or {},
        },
    }


def _write_artifact(tmp_path: Path, *, payload: dict[str, Any] | None = None) -> Any:
    path = tmp_path / "candidates.json"
    path.write_text(
        json.dumps(make_event_artifact_payload() if payload is None else payload),
        encoding="utf-8",
    )
    return load_event_paper_artifact(path)


async def _run(
    *,
    tmp_path: Path,
    pubsub: _PubSubRouter,
    supabase: _SupabaseRouter,
    now: datetime = datetime(2026, 1, 21, 0, 1, tzinfo=UTC),
    wall_clock: Callable[[], datetime] | None = None,
    artifact_payload: dict[str, Any] | None = None,
    execution_candidate_id: str | None = None,
) -> Any:
    transport_pubsub = httpx.MockTransport(pubsub)
    transport_supabase = httpx.MockTransport(supabase)
    async with (
        PubSubSubscriber(
            project_id="trade-ai-dev",
            emulator_host="pubsub:8085",
            transport=transport_pubsub,
        ) as subscriber,
        PubSubPublisher(
            project_id="trade-ai-dev",
            emulator_host="pubsub:8085",
            transport=transport_pubsub,
        ) as publisher,
        EventPaperSupabaseClient(
            url="https://example.supabase.co",
            secret_key="service-key",
            transport=transport_supabase,
        ) as client,
    ):
        return await EventPaperPublisherRunner(
            artifact=_write_artifact(tmp_path, payload=artifact_payload),
            target_date=TARGET_DATE,
            subscriber=subscriber,
            publisher=publisher,
            supabase=client,
            execution_candidate_id=execution_candidate_id,
            config=EventPaperPublishConfig(max_pull_batches=2, idle_backoff_seconds=0),
            wall_clock=wall_clock or (lambda: now),
        ).run()


async def test_event_supabase_client_ignores_proxy_environment() -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=[]))

    async with EventPaperSupabaseClient(
        url="http://127.0.0.1:54321",
        secret_key="service-key",
        transport=transport,
    ) as client:
        assert client._started_client()._trust_env is False


async def test_publisher_uses_fresh_best_ask_and_paper_only_identity(tmp_path: Path) -> None:
    book = make_event_book()
    events: list[str] = []
    pubsub = _PubSubRouter(
        pull_batches=[
            {
                "receivedMessages": [
                    _pull_message(
                        ack_id="ack-book",
                        message_id="raw-book-1",
                        payload=book.model_dump_json().encode(),
                        attributes={"kind": "book", "symbol": "7203"},
                    )
                ]
            }
        ],
        events=events,
    )
    supabase = _SupabaseRouter(events=events)

    receipt = await _run(tmp_path=tmp_path, pubsub=pubsub, supabase=supabase)

    assert pubsub.sought == 1
    assert pubsub.acked == ["ack-book"]
    assert len(pubsub.published) == 1
    message = json.loads(pubsub.published[0].content.decode())["messages"][0]
    signal = StrategySignal.model_validate_json(base64.b64decode(message["data"]))
    assert signal.source is SignalSource.RULE
    assert signal.routing_intent is RoutingIntent.PAPER_ONLY
    assert signal.strategy_key == EVENT_EXECUTION_STRATEGY_KEY
    signal_claim = parse_claim_json(signal.reasoning)
    assert signal_claim.execution_profile == EVENT_EXECUTION_PROFILE
    assert signal_claim.comparable_to_registered_backtest is False
    assert signal.candidate_id == "cluster-7203:obs-7203"
    assert signal.action is Action.BUY
    assert signal.holding_type is TradingStyle.SWING
    assert str(signal.price) == "1000"
    assert str(signal.stop_loss_pct) == "0.10"
    assert signal.stop_loss_price is None
    assert signal.max_hold_days == 20
    assert signal.created_at == book.received_at
    assert signal.best_bid == 999
    assert signal.best_ask == 1000
    assert message["attributes"]["routing_intent"] == "PAPER_ONLY"
    assert receipt.published[0].observed_ask == 1000
    assert receipt.execution_profile == EVENT_EXECUTION_PROFILE
    assert receipt.comparable_to_registered_backtest is False
    assert len(supabase.strategy_logs) == 1
    assert events == ["claim", "publish"]


async def test_publisher_acks_stale_book_then_uses_fresh_book(tmp_path: Path) -> None:
    now = datetime(2026, 1, 21, 0, 1, tzinfo=UTC)
    stale = make_event_book(received_at=now - timedelta(seconds=11), best_ask="900")
    fresh = make_event_book(received_at=now, best_ask="1000")
    pubsub = _PubSubRouter(
        pull_batches=[
            {
                "receivedMessages": [
                    _pull_message(
                        ack_id="stale",
                        message_id="old",
                        payload=stale.model_dump_json().encode(),
                        attributes={"kind": "book", "symbol": "7203"},
                    ),
                    _pull_message(
                        ack_id="fresh",
                        message_id="new",
                        payload=fresh.model_dump_json().encode(),
                        attributes={"kind": "book", "symbol": "7203"},
                    ),
                ]
            }
        ]
    )

    receipt = await _run(
        tmp_path=tmp_path,
        pubsub=pubsub,
        supabase=_SupabaseRouter(),
        now=now,
    )

    assert pubsub.acked == ["stale", "fresh"]
    assert receipt.skipped_messages == {"stale_book": 1}
    assert receipt.published[0].observed_ask == 1000


def _multi_candidate_payload() -> dict[str, Any]:
    second = make_event_candidate(
        execution_candidate_id="cluster-6758:obs-6758",
        cluster_id="cluster-6758",
        observation_id="obs-6758",
        symbol="6758",
        symbol_name="ソニーグループ",
    )
    return make_event_artifact_payload(candidates=[make_event_candidate(), second])


async def test_multi_candidate_artifact_requires_explicit_occurrence_before_io(
    tmp_path: Path,
) -> None:
    pubsub = _PubSubRouter()
    supabase = _SupabaseRouter()

    with pytest.raises(EventPaperPublishError, match="exactly one event occurrence"):
        await _run(
            tmp_path=tmp_path,
            pubsub=pubsub,
            supabase=supabase,
            artifact_payload=_multi_candidate_payload(),
        )

    assert supabase.requests == []
    assert pubsub.sought == 0
    assert pubsub.published == []


async def test_multi_candidate_artifact_publishes_only_selected_occurrence(
    tmp_path: Path,
) -> None:
    book = make_event_book(symbol="6758")
    pubsub = _PubSubRouter(
        pull_batches=[
            {
                "receivedMessages": [
                    _pull_message(
                        ack_id="selected",
                        message_id="raw-6758",
                        payload=book.model_dump_json().encode(),
                        attributes={"kind": "book", "symbol": "6758"},
                    )
                ]
            }
        ]
    )

    receipt = await _run(
        tmp_path=tmp_path,
        pubsub=pubsub,
        supabase=_SupabaseRouter(),
        artifact_payload=_multi_candidate_payload(),
        execution_candidate_id="cluster-6758:obs-6758",
    )

    assert receipt.selected_execution_candidate_ids == ["cluster-6758:obs-6758"]
    assert [record.symbol for record in receipt.published] == ["6758"]


@pytest.mark.parametrize(
    ("supabase", "match"),
    [
        (_SupabaseRouter(trade_mode="live"), "trade_mode=paper"),
        (_SupabaseRouter(allowed=False), "trading to be allowed"),
        (_SupabaseRouter(due_positions=[{"symbol": "6758"}]), "due paper swing exits"),
    ],
)
async def test_publisher_preflight_failure_has_no_pubsub_side_effect(
    tmp_path: Path,
    supabase: _SupabaseRouter,
    match: str,
) -> None:
    pubsub = _PubSubRouter()

    with pytest.raises(EventPaperPublishError, match=match):
        await _run(tmp_path=tmp_path, pubsub=pubsub, supabase=supabase)

    assert pubsub.sought == 0
    assert pubsub.published == []
    assert pubsub.acked == []
    assert supabase.strategy_logs == {}


async def test_existing_claim_republishes_exact_original_quote(tmp_path: Path) -> None:
    artifact = _write_artifact(tmp_path)
    first_book = make_event_book(best_ask="1000")
    from strategy_rule.event_paper.publisher import build_signal_claim

    candidate = artifact.artifact.candidates[0]
    _claim, signal = build_signal_claim(
        candidate=candidate,
        book=first_book,
        raw_book_message_id="original-book",
        artifact_sha256=artifact.sha256,
        config=EventPaperPublishConfig(),
    )
    supabase = _SupabaseRouter(
        strategy_logs={
            str(signal.signal_id): {
                "signal_id": str(signal.signal_id),
                "source": "RULE",
                "symbol": "7203",
                "action": "BUY",
                "confidence": 0.5,
                "reasoning": signal.reasoning,
                "created_at": signal.created_at.isoformat(),
            }
        }
    )
    newer_book = make_event_book(best_ask="1100")
    pubsub = _PubSubRouter(
        pull_batches=[
            {
                "receivedMessages": [
                    _pull_message(
                        ack_id="newer",
                        message_id="newer-book",
                        payload=newer_book.model_dump_json().encode(),
                        attributes={"kind": "book", "symbol": "7203"},
                    )
                ]
            }
        ]
    )

    receipt = await _run(tmp_path=tmp_path, pubsub=pubsub, supabase=supabase)

    assert receipt.published[0].observed_ask == 1000
    assert pubsub.sought == 0
    assert pubsub.acked == []
    assert len(pubsub.published) == 1


async def test_concurrent_publication_attempt_does_not_publish_twice(tmp_path: Path) -> None:
    artifact = _write_artifact(tmp_path)
    from strategy_rule.event_paper.publisher import build_signal_claim

    claim, signal = build_signal_claim(
        candidate=artifact.artifact.candidates[0],
        book=make_event_book(),
        raw_book_message_id="original-book",
        artifact_sha256=artifact.sha256,
        config=EventPaperPublishConfig(),
    )
    foreign_claim = EventPaperSignalClaim.model_validate(
        {
            **claim.model_dump(mode="python"),
            "publication_attempt": EventPaperPublicationAttempt(
                attempt_id="foreign-attempt",
                attempted_at=datetime(2026, 1, 21, 0, 1, tzinfo=UTC),
            ),
        }
    )
    supabase = _SupabaseRouter(
        strategy_logs={
            str(signal.signal_id): {
                "signal_id": str(signal.signal_id),
                "source": "RULE",
                "symbol": signal.symbol,
                "action": "BUY",
                "confidence": signal.confidence,
                "reasoning": signal.reasoning,
                "created_at": signal.created_at.isoformat(),
            }
        }
    )

    def _win_attempt_race(row: dict[str, Any]) -> None:
        row["reasoning"] = claim_json(foreign_claim)
        supabase.on_cas = None

    supabase.on_cas = _win_attempt_race
    pubsub = _PubSubRouter()

    with pytest.raises(EventPaperPublishError, match="owned by another invocation"):
        await _run(tmp_path=tmp_path, pubsub=pubsub, supabase=supabase)

    assert pubsub.published == []
    assert pubsub.acked == []


async def test_ack_failure_leaves_claim_recoverable_without_ambiguous_attempt(
    tmp_path: Path,
) -> None:
    book = make_event_book()
    pubsub = _PubSubRouter(
        pull_batches=[
            {
                "receivedMessages": [
                    _pull_message(
                        ack_id="book",
                        message_id="raw-book",
                        payload=book.model_dump_json().encode(),
                        attributes={"kind": "book", "symbol": "7203"},
                    )
                ]
            }
        ],
        ack_error=RuntimeError("injected ack failure"),
    )
    supabase = _SupabaseRouter()

    with pytest.raises(EventPaperPublishError, match="acknowledge failed"):
        await _run(tmp_path=tmp_path, pubsub=pubsub, supabase=supabase)

    [row] = supabase.strategy_logs.values()
    stored_claim = parse_claim_json(str(row["reasoning"]))
    assert stored_claim.publication_attempt is None
    assert pubsub.published == []

    pubsub.ack_error = None
    recovered = await _run(tmp_path=tmp_path, pubsub=pubsub, supabase=supabase)

    assert recovered.published[0].publication_status == "confirmed"
    assert pubsub.publish_attempts == 1


async def test_checkpointed_publication_reconstructs_receipt_outside_entry_window(
    tmp_path: Path,
) -> None:
    book = make_event_book()
    pubsub = _PubSubRouter(
        pull_batches=[
            {
                "receivedMessages": [
                    _pull_message(
                        ack_id="book",
                        message_id="raw-book",
                        payload=book.model_dump_json().encode(),
                        attributes={"kind": "book", "symbol": "7203"},
                    )
                ]
            }
        ]
    )
    supabase = _SupabaseRouter()

    first = await _run(tmp_path=tmp_path, pubsub=pubsub, supabase=supabase)
    second = await _run(
        tmp_path=tmp_path,
        pubsub=pubsub,
        supabase=supabase,
        now=datetime(2026, 1, 22, 0, 1, tzinfo=UTC),
    )

    assert first == second
    assert pubsub.publish_attempts == 1
    assert pubsub.sought == 1


async def test_ambiguous_publish_checkpoint_failure_is_reportable_without_republish(
    tmp_path: Path,
) -> None:
    book = make_event_book()
    pubsub = _PubSubRouter(
        pull_batches=[
            {
                "receivedMessages": [
                    _pull_message(
                        ack_id="book",
                        message_id="raw-book",
                        payload=book.model_dump_json().encode(),
                        attributes={"kind": "book", "symbol": "7203"},
                    )
                ]
            }
        ]
    )
    supabase = _SupabaseRouter(cas_statuses=[200, 500])

    with pytest.raises(EventPaperPublishError, match="published but checkpoint failed"):
        await _run(tmp_path=tmp_path, pubsub=pubsub, supabase=supabase)

    recovered = await _run(
        tmp_path=tmp_path,
        pubsub=pubsub,
        supabase=supabase,
        now=datetime(2026, 1, 22, 0, 1, tzinfo=UTC),
    )

    [record] = recovered.published
    assert record.publication_status == "ambiguous"
    assert record.strategy_message_id is None
    assert record.published_at is None
    assert pubsub.publish_attempts == 1
    assert pubsub.sought == 1


async def test_existing_claim_row_must_match_durable_payload(tmp_path: Path) -> None:
    artifact = _write_artifact(tmp_path)
    from strategy_rule.event_paper.publisher import build_signal_claim

    _claim, signal = build_signal_claim(
        candidate=artifact.artifact.candidates[0],
        book=make_event_book(),
        raw_book_message_id="original-book",
        artifact_sha256=artifact.sha256,
        config=EventPaperPublishConfig(),
    )
    supabase = _SupabaseRouter(
        strategy_logs={
            str(signal.signal_id): {
                "signal_id": str(signal.signal_id),
                "source": "AI",
                "symbol": signal.symbol,
                "action": "BUY",
                "confidence": signal.confidence,
                "reasoning": signal.reasoning,
                "created_at": signal.created_at.isoformat(),
            }
        }
    )

    with pytest.raises(EventPaperPublishError, match="does not match its payload"):
        await _run(tmp_path=tmp_path, pubsub=_PubSubRouter(), supabase=supabase)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda claim: claim["signal_fields"].update(symbol="6758"),
        lambda claim: claim.update(event_ids=["different-event"]),
        lambda claim: claim["signal_fields"].update(scheduled_exit_date="2026-01-22"),
    ],
)
async def test_existing_claim_is_fully_bound_to_candidate(
    tmp_path: Path,
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    artifact = _write_artifact(tmp_path)
    from strategy_rule.event_paper.publisher import build_signal_claim

    _claim, signal = build_signal_claim(
        candidate=artifact.artifact.candidates[0],
        book=make_event_book(),
        raw_book_message_id="original-book",
        artifact_sha256=artifact.sha256,
        config=EventPaperPublishConfig(),
    )
    reasoning = json.loads(signal.reasoning or "{}")
    mutate(reasoning)
    supabase = _SupabaseRouter(
        strategy_logs={
            str(signal.signal_id): {
                "signal_id": str(signal.signal_id),
                "source": "RULE",
                "symbol": signal.symbol,
                "action": "BUY",
                "confidence": signal.confidence,
                "reasoning": json.dumps(reasoning),
                "created_at": signal.created_at.isoformat(),
            }
        }
    )

    with pytest.raises(EventPaperPublishError, match="claim"):
        await _run(tmp_path=tmp_path, pubsub=_PubSubRouter(), supabase=supabase)


@pytest.mark.parametrize(
    ("book", "now", "expected"),
    [
        (
            make_event_book().model_copy(update={"received_at": None}),
            datetime(2026, 1, 21, 0, 1, tzinfo=UTC),
            "missing_received_at",
        ),
        (
            make_event_book(received_at=datetime(2026, 1, 21, 0, 1, 6, tzinfo=UTC)),
            datetime(2026, 1, 21, 0, 1, tzinfo=UTC),
            "future_book",
        ),
        (
            make_event_book(received_at=datetime(2026, 1, 20, 23, 59, tzinfo=UTC)),
            datetime(2026, 1, 21, 0, 1, tzinfo=UTC),
            "book_before_entry_window",
        ),
        (
            make_event_book().model_copy(update={"asks": []}),
            datetime(2026, 1, 21, 0, 1, tzinfo=UTC),
            "missing_ask",
        ),
        (
            make_event_book(best_bid="1000", best_ask="1000"),
            datetime(2026, 1, 21, 0, 1, tzinfo=UTC),
            "crossed_or_locked_book",
        ),
    ],
)
def test_book_gate_rejects_unsafe_execution_quote(
    tmp_path: Path,
    book: Any,
    now: datetime,
    expected: str,
) -> None:
    candidate = _write_artifact(tmp_path).artifact.candidates[0]
    assert (
        book_rejection_reason(
            book=book,
            candidate=candidate,
            now=now,
            config=EventPaperPublishConfig(),
        )
        == expected
    )


@pytest.mark.parametrize(
    "override",
    [
        {"max_book_age_seconds": 11},
        {"max_future_skew_seconds": 4},
        {"entry_window_end": time(9, 31)},
    ],
)
def test_event_publish_config_freezes_timing_contract(override: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="frozen"):
        EventPaperPublishConfig(**override)


async def test_allowed_future_skew_can_complete_durable_publication(tmp_path: Path) -> None:
    now = datetime(2026, 1, 21, 0, 1, tzinfo=UTC)
    book = make_event_book(received_at=now + timedelta(seconds=4))
    pubsub = _PubSubRouter(
        pull_batches=[
            {
                "receivedMessages": [
                    _pull_message(
                        ack_id="book",
                        message_id="future-within-tolerance",
                        payload=book.model_dump_json().encode(),
                        attributes={"kind": "book", "symbol": "7203"},
                    )
                ]
            }
        ]
    )

    receipt = await _run(
        tmp_path=tmp_path,
        pubsub=pubsub,
        supabase=_SupabaseRouter(),
        now=now,
    )

    assert receipt.published[0].publication_status == "confirmed"


def test_entry_window_is_fixed_to_target_jst_date() -> None:
    config = EventPaperPublishConfig()
    assert (
        entry_window_rejection(
            now=datetime(2026, 1, 20, 23, 59, tzinfo=UTC),
            target_date=TARGET_DATE,
            config=config,
        )
        == "before_entry_window"
    )
    assert (
        entry_window_rejection(
            now=datetime(2026, 1, 21, 0, 30, tzinfo=UTC),
            target_date=TARGET_DATE,
            config=config,
        )
        == "after_entry_window"
    )


async def test_mode_change_after_claim_cannot_publish_or_ack(tmp_path: Path) -> None:
    book = make_event_book()
    pubsub = _PubSubRouter(
        pull_batches=[
            {
                "receivedMessages": [
                    _pull_message(
                        ack_id="book",
                        message_id="raw-book",
                        payload=book.model_dump_json().encode(),
                        attributes={"kind": "book", "symbol": "7203"},
                    )
                ]
            }
        ]
    )
    supabase = _SupabaseRouter(trade_mode_responses=["paper", "paper", "live"])

    with pytest.raises(EventPaperPublishError, match="trade_mode=paper"):
        await _run(tmp_path=tmp_path, pubsub=pubsub, supabase=supabase)

    assert len(supabase.strategy_logs) == 1
    assert pubsub.published == []
    assert pubsub.acked == []


async def test_quote_aging_during_final_mode_check_cannot_publish_or_ack(
    tmp_path: Path,
) -> None:
    selected_at = datetime(2026, 1, 21, 0, 1, tzinfo=UTC)
    now = [selected_at]
    book = make_event_book(received_at=selected_at)
    pubsub = _PubSubRouter(
        pull_batches=[
            {
                "receivedMessages": [
                    _pull_message(
                        ack_id="book",
                        message_id="raw-book",
                        payload=book.model_dump_json().encode(),
                        attributes={"kind": "book", "symbol": "7203"},
                    )
                ]
            }
        ]
    )

    def _advance_after_final_mode_read(reads: int) -> None:
        if reads == 3:
            now[0] = selected_at + timedelta(seconds=11)

    supabase = _SupabaseRouter(on_system_status=_advance_after_final_mode_read)

    with pytest.raises(EventPaperPublishError, match="stale_book"):
        await _run(
            tmp_path=tmp_path,
            pubsub=pubsub,
            supabase=supabase,
            wall_clock=lambda: now[0],
        )

    assert len(supabase.strategy_logs) == 1
    assert pubsub.published == []
    assert pubsub.acked == []


async def test_due_exit_appearing_after_claim_blocks_final_publish(tmp_path: Path) -> None:
    book = make_event_book()
    pubsub = _PubSubRouter(
        pull_batches=[
            {
                "receivedMessages": [
                    _pull_message(
                        ack_id="book",
                        message_id="raw-book",
                        payload=book.model_dump_json().encode(),
                        attributes={"kind": "book", "symbol": "7203"},
                    )
                ]
            }
        ]
    )
    supabase = _SupabaseRouter(
        due_position_responses=[[], [{"symbol": "6758"}]],
    )

    with pytest.raises(EventPaperPublishError, match="due paper swing exits"):
        await _run(tmp_path=tmp_path, pubsub=pubsub, supabase=supabase)

    assert len(supabase.strategy_logs) == 1
    assert pubsub.published == []
    assert pubsub.acked == []


async def test_pubsub_transport_failure_is_not_automatically_retried(tmp_path: Path) -> None:
    selected_at = datetime(2026, 1, 21, 0, 1, tzinfo=UTC)
    book = make_event_book(received_at=selected_at)

    pubsub = _PubSubRouter(
        pull_batches=[
            {
                "receivedMessages": [
                    _pull_message(
                        ack_id="book",
                        message_id="raw-book",
                        payload=book.model_dump_json().encode(),
                        attributes={"kind": "book", "symbol": "7203"},
                    )
                ]
            }
        ],
        publish_statuses=[500, 200],
    )
    supabase = _SupabaseRouter()

    with pytest.raises(EventPaperPublishError, match="automatic retry disabled"):
        await _run(
            tmp_path=tmp_path,
            pubsub=pubsub,
            supabase=supabase,
        )

    assert pubsub.publish_attempts == 1
    assert pubsub.acked == ["book"]

    recovered = await _run(
        tmp_path=tmp_path,
        pubsub=pubsub,
        supabase=supabase,
        now=datetime(2026, 1, 22, 0, 1, tzinfo=UTC),
    )

    assert recovered.published[0].publication_status == "ambiguous"
    assert pubsub.publish_attempts == 1
