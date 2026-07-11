"""Fail-closed Supabase preflight and durable signal-claim client."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Self
from uuid import UUID

import httpx
from trade_contracts.signal import StrategySignal

from .models import (
    EVENT_SIGNAL_CONFIDENCE,
    EventPaperPreflightState,
    EventPaperPublicationAttempt,
    EventPaperPublicationCheckpoint,
    EventPaperSignalClaim,
    claim_json,
    parse_claim_json,
)


class EventPaperSupabaseError(RuntimeError):
    """Raised when publication safety cannot be proven through Supabase."""


@dataclass(slots=True)
class EventPaperSupabaseClient:
    url: str
    secret_key: str
    timeout_seconds: float = 30.0
    transport: httpx.AsyncBaseTransport | None = None
    _client: httpx.AsyncClient | None = field(default=None, init=False)

    async def __aenter__(self) -> Self:
        self._client = httpx.AsyncClient(
            base_url=self.url.rstrip("/"),
            timeout=self.timeout_seconds,
            headers={
                "apikey": self.secret_key,
                "Authorization": f"Bearer {self.secret_key}",
                "Content-Type": "application/json",
            },
            transport=self.transport,
            trust_env=False,
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client is not None:
            await self._client.aclose()
        self._client = None

    def _started_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise EventPaperSupabaseError("Supabase client is not started")
        return self._client

    async def preflight(self, *, target_date: date) -> EventPaperPreflightState:
        """Verify target capabilities, paper mode, and exit-before-entry ordering."""

        await self._probe_atomic_capabilities()
        return await self.assert_entry_ready(target_date=target_date)

    async def assert_entry_ready(self, *, target_date: date) -> EventPaperPreflightState:
        """Recheck mutable paper mode and exit-before-entry state."""

        state = await self.assert_paper_mode()
        due_symbols = await self._read_due_swing_symbols(target_date=target_date)
        if due_symbols:
            raise EventPaperSupabaseError(
                "due paper swing exits remain unresolved: " + ",".join(due_symbols)
            )
        missing_schedule = await self._read_missing_schedule_symbols()
        if missing_schedule:
            raise EventPaperSupabaseError(
                "paper swing positions have no scheduled_exit_date: " + ",".join(missing_schedule)
            )
        return EventPaperPreflightState(
            trade_mode=state.trade_mode,
            is_trading_allowed=state.is_trading_allowed,
            due_symbols=(),
        )

    async def assert_paper_mode(self) -> EventPaperPreflightState:
        client = self._started_client()
        response = await client.get(
            "/rest/v1/system_status",
            params={
                "select": "trade_mode,is_trading_allowed",
                "id": "eq.1",
                "limit": "1",
            },
        )
        self._raise_for_status(response, operation="read system_status")
        rows = self._json_rows(response, operation="read system_status")
        if len(rows) != 1:
            raise EventPaperSupabaseError("system_status id=1 is missing or duplicated")
        row = rows[0]
        trade_mode = str(row.get("trade_mode", ""))
        allowed = row.get("is_trading_allowed") is True
        if trade_mode != "paper":
            raise EventPaperSupabaseError(
                f"event paper publish requires trade_mode=paper, got {trade_mode or '<missing>'}"
            )
        if not allowed:
            raise EventPaperSupabaseError("event paper publish requires trading to be allowed")
        return EventPaperPreflightState(
            trade_mode=trade_mode,
            is_trading_allowed=allowed,
        )

    async def read_claim_reasoning(self, *, signal_id: UUID) -> str | None:
        client = self._started_client()
        response = await client.get(
            "/rest/v1/strategy_logs",
            params={
                "select": "signal_id,source,symbol,action,confidence,reasoning,created_at",
                "signal_id": f"eq.{signal_id}",
                "limit": "1",
            },
        )
        self._raise_for_status(response, operation="read strategy signal claim")
        rows = self._json_rows(response, operation="read strategy signal claim")
        if not rows:
            return None
        if len(rows) != 1:
            raise EventPaperSupabaseError(f"duplicate strategy log rows for signal_id={signal_id}")
        row = rows[0]
        reasoning = row.get("reasoning")
        if reasoning is not None and not isinstance(reasoning, str):
            raise EventPaperSupabaseError("strategy signal claim reasoning is not text")
        try:
            claim = parse_claim_json(reasoning)
            stored_created_at = datetime.fromisoformat(str(row.get("created_at")))
            stored_confidence = Decimal(str(row.get("confidence")))
        except (InvalidOperation, ValueError) as exc:
            raise EventPaperSupabaseError("strategy signal claim row is malformed") from exc
        expected = claim.signal_fields
        if (
            str(row.get("signal_id")) != str(signal_id)
            or row.get("source") != "RULE"
            or row.get("symbol") != expected.symbol
            or row.get("action") != "BUY"
            or stored_confidence != Decimal(str(EVENT_SIGNAL_CONFIDENCE))
            or stored_created_at != expected.created_at
        ):
            raise EventPaperSupabaseError("strategy signal claim row does not match its payload")
        return reasoning

    async def claim_signal(self, signal: StrategySignal) -> str:
        """Insert-once the selected quote, then return the authoritative claim."""

        client = self._started_client()
        row = _signal_log_row(signal)
        response = await client.post(
            "/rest/v1/strategy_logs",
            params={"on_conflict": "signal_id"},
            headers={"Prefer": "resolution=ignore-duplicates,return=minimal"},
            json=[row],
        )
        self._raise_for_status(response, operation="claim strategy signal")

        verify = await client.get(
            "/rest/v1/strategy_logs",
            params={
                "select": "signal_id,source,symbol,action,confidence,reasoning,created_at",
                "signal_id": f"eq.{signal.signal_id}",
                "limit": "1",
            },
        )
        self._raise_for_status(verify, operation="verify strategy signal claim")
        rows = self._json_rows(verify, operation="verify strategy signal claim")
        if len(rows) != 1:
            raise EventPaperSupabaseError("strategy signal claim was not persisted exactly once")
        stored = rows[0]
        expected = row
        for key in ("signal_id", "source", "symbol", "action"):
            if str(stored.get(key)) != str(expected[key]):
                raise EventPaperSupabaseError(f"strategy signal claim mismatch: {key}")
        if Decimal(str(stored.get("confidence"))) != Decimal(str(expected["confidence"])):
            raise EventPaperSupabaseError("strategy signal claim mismatch: confidence")
        try:
            stored_created_at = datetime.fromisoformat(str(stored.get("created_at")))
        except ValueError as exc:
            raise EventPaperSupabaseError("strategy signal claim has invalid created_at") from exc
        if stored_created_at != signal.created_at:
            raise EventPaperSupabaseError("strategy signal claim mismatch: created_at")
        reasoning = stored.get("reasoning")
        if not isinstance(reasoning, str):
            raise EventPaperSupabaseError("strategy signal claim has no durable reasoning")
        return reasoning

    async def checkpoint_publication(
        self,
        *,
        signal_id: UUID,
        claim: EventPaperSignalClaim,
        strategy_message_id: str,
        published_at: datetime,
    ) -> EventPaperSignalClaim:
        """Persist Pub/Sub success so a receipt can be reconstructed after a crash."""

        attempt = claim.publication_attempt
        if attempt is None:
            raise EventPaperSupabaseError("publication checkpoint has no durable attempt")
        checkpoint = EventPaperPublicationCheckpoint(
            attempt_id=attempt.attempt_id,
            strategy_message_id=strategy_message_id,
            published_at=published_at,
        )
        try:
            updated = EventPaperSignalClaim.model_validate(
                {
                    **claim.model_dump(mode="python"),
                    "publication": checkpoint,
                }
            )
        except ValueError as exc:
            raise EventPaperSupabaseError(
                "strategy signal publication checkpoint violates the entry contract"
            ) from exc
        authoritative = await self._update_claim_cas(
            signal_id=signal_id,
            previous=claim,
            updated=updated,
            operation="checkpoint strategy signal publication",
        )
        if (
            authoritative.publication is None
            or authoritative.publication.attempt_id != attempt.attempt_id
        ):
            raise EventPaperSupabaseError("strategy signal publication checkpoint is missing")
        return authoritative

    async def begin_publication_attempt(
        self,
        *,
        signal_id: UUID,
        claim: EventPaperSignalClaim,
        attempt_id: str,
        attempted_at: datetime,
    ) -> EventPaperSignalClaim:
        """Durably mark an ambiguous external attempt before ack/publish."""

        if claim.publication_attempt is not None:
            return claim
        attempt = EventPaperPublicationAttempt(
            attempt_id=attempt_id,
            attempted_at=attempted_at,
        )
        try:
            updated = EventPaperSignalClaim.model_validate(
                {
                    **claim.model_dump(mode="python"),
                    "publication_attempt": attempt,
                }
            )
        except ValueError as exc:
            raise EventPaperSupabaseError(
                "strategy signal publication attempt violates the entry contract"
            ) from exc
        authoritative = await self._update_claim_cas(
            signal_id=signal_id,
            previous=claim,
            updated=updated,
            operation="begin strategy signal publication attempt",
        )
        if authoritative.publication_attempt is None:
            raise EventPaperSupabaseError("strategy signal publication attempt is missing")
        if (
            authoritative.publication is None
            and authoritative.publication_attempt.attempt_id != attempt_id
        ):
            raise EventPaperSupabaseError(
                "strategy signal publication attempt is owned by another invocation"
            )
        return authoritative

    async def _update_claim_cas(
        self,
        *,
        signal_id: UUID,
        previous: EventPaperSignalClaim,
        updated: EventPaperSignalClaim,
        operation: str,
    ) -> EventPaperSignalClaim:
        client = self._started_client()
        try:
            response = await client.post(
                "/rest/v1/rpc/event_paper_cas_strategy_reasoning",
                json={
                    "p_signal_id": str(signal_id),
                    "p_expected_reasoning": claim_json(previous),
                    "p_updated_reasoning": claim_json(updated),
                },
            )
        except httpx.HTTPError as exc:
            raise EventPaperSupabaseError(f"{operation} request failed: {exc}") from exc
        self._raise_for_status(response, operation=operation)
        rows = self._json_rows(response, operation=operation)
        if (
            len(rows) != 1
            or not isinstance(rows[0].get("applied"), bool)
            or not isinstance(rows[0].get("reasoning"), str)
        ):
            raise EventPaperSupabaseError(f"{operation} returned an invalid CAS result")
        reasoning = rows[0]["reasoning"]
        if rows[0]["applied"] is True and reasoning != claim_json(updated):
            raise EventPaperSupabaseError(f"{operation} returned the wrong updated claim")
        try:
            authoritative = parse_claim_json(reasoning)
        except ValueError as exc:
            raise EventPaperSupabaseError(f"{operation} returned a malformed claim") from exc
        if _claim_without_progress(authoritative) != _claim_without_progress(previous):
            raise EventPaperSupabaseError(f"{operation} encountered a different base claim")
        return authoritative

    async def _probe_atomic_capabilities(self) -> None:
        client = self._started_client()
        column = await client.get(
            "/rest/v1/trades_paper",
            params={"select": "order_id", "limit": "0"},
        )
        self._raise_for_status(column, operation="probe trades_paper.order_id")
        close_session_column = await client.get(
            "/rest/v1/positions",
            params={"select": "scheduled_exit_time", "limit": "0"},
        )
        self._raise_for_status(
            close_session_column, operation="probe positions.scheduled_exit_time"
        )
        probes: tuple[tuple[str, dict[str, object], str], ...] = (
            (
                "event_paper_cas_strategy_reasoning",
                {
                    "p_signal_id": None,
                    "p_expected_reasoning": None,
                    "p_updated_reasoning": None,
                },
                "p_signal_id is required",
            ),
            (
                "oms_paper_apply_fill",
                {
                    "p_order_id": None,
                    "p_trade_id": None,
                    "p_symbol": None,
                    "p_side": None,
                    "p_filled_quantity": None,
                    "p_fill_price": None,
                    "p_signal_source": None,
                    "p_unified_signal_id": None,
                    "p_executed_at": None,
                    "p_expected_position_opened_at": None,
                    "p_new_holding_type": None,
                    "p_new_target_price": None,
                    "p_new_stop_loss_price": None,
                    "p_new_max_hold_days": None,
                    "p_new_scheduled_exit_date": None,
                    "p_new_scheduled_exit_time": None,
                    "p_new_trailing_stop_pct": None,
                },
                "p_order_id and p_trade_id are required",
            ),
            (
                "oms_paper_update_stop_loss",
                {
                    "p_symbol": None,
                    "p_expected_position_opened_at": None,
                    "p_stop_loss_price": None,
                },
                "p_symbol is required",
            ),
        )
        for name, payload, expected in probes:
            response = await client.post(f"/rest/v1/rpc/{name}", json=payload)
            if response.status_code != 400 or expected not in response.text:
                raise EventPaperSupabaseError(
                    f"required RPC unavailable: {name} status={response.status_code} "
                    f"body={response.text[:160]}"
                )

    async def _read_due_swing_symbols(self, *, target_date: date) -> tuple[str, ...]:
        client = self._started_client()
        response = await client.get(
            "/rest/v1/positions",
            params={
                "select": "symbol",
                "trade_type": "eq.paper",
                "holding_type": "eq.swing",
                "scheduled_exit_date": f"lte.{target_date.isoformat()}",
                "order": "symbol.asc",
            },
        )
        self._raise_for_status(response, operation="read due paper swing positions")
        rows = self._json_rows(response, operation="read due paper swing positions")
        return tuple(str(row.get("symbol", "")) for row in rows if row.get("symbol"))

    async def _read_missing_schedule_symbols(self) -> tuple[str, ...]:
        client = self._started_client()
        response = await client.get(
            "/rest/v1/positions",
            params={
                "select": "symbol",
                "trade_type": "eq.paper",
                "holding_type": "eq.swing",
                "scheduled_exit_date": "is.null",
                "max_hold_days": "not.is.null",
                "order": "symbol.asc",
            },
        )
        self._raise_for_status(response, operation="read unscheduled paper swing positions")
        rows = self._json_rows(response, operation="read unscheduled paper swing positions")
        return tuple(str(row.get("symbol", "")) for row in rows if row.get("symbol"))

    @staticmethod
    def _raise_for_status(response: httpx.Response, *, operation: str) -> None:
        if response.status_code >= 300:
            raise EventPaperSupabaseError(
                f"{operation} failed: status={response.status_code} body={response.text[:200]}"
            )

    @staticmethod
    def _json_rows(response: httpx.Response, *, operation: str) -> list[dict[str, Any]]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise EventPaperSupabaseError(f"{operation} returned malformed JSON") from exc
        if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
            raise EventPaperSupabaseError(f"{operation} returned an unexpected payload")
        return payload


def _signal_log_row(signal: StrategySignal) -> dict[str, Any]:
    return {
        "signal_id": str(signal.signal_id),
        "source": signal.source.value,
        "symbol": signal.symbol,
        "action": signal.action.value,
        "confidence": signal.confidence,
        "reasoning": signal.reasoning,
        "created_at": signal.created_at.isoformat(),
    }


def _claim_without_progress(claim: EventPaperSignalClaim) -> EventPaperSignalClaim:
    return claim.model_copy(
        update={
            "publication_attempt": None,
            "publication": None,
        }
    )
