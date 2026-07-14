"""One-shot event-paper publisher over a dedicated raw-book subscription."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from trade_contracts.enums import Action, SignalSource
from trade_contracts.market import OrderBookSnapshot
from trade_contracts.pubsub_client import PubSubPublisher, PubSubSubscriber, PulledMessage
from trade_contracts.signal import StrategySignal, deterministic_strategy_signal_id

from .artifact import EventPaperCandidate, LoadedEventPaperArtifact
from .models import (
    EventPaperPublishConfig,
    EventPaperPublishedRecord,
    EventPaperPublishReceipt,
    EventPaperSignalClaim,
)
from .publisher import (
    book_rejection_reason,
    build_signal_claim,
    entry_window_rejection,
    signal_from_claim,
)
from .supabase import EventPaperSupabaseClient, EventPaperSupabaseError

WallClock = Callable[[], datetime]
Sleep = Callable[[float], Awaitable[None]]
JST = ZoneInfo("Asia/Tokyo")


class EventPaperPublishError(RuntimeError):
    """Raised when the one-shot publisher cannot safely complete."""


class _PublicationAlreadyResolved(RuntimeError):
    """Internal control flow for a concurrent checkpoint winner."""

    def __init__(self, claim: EventPaperSignalClaim) -> None:
        super().__init__("publication was resolved by another invocation")
        self.claim = claim


@dataclass(slots=True)
class EventPaperPublisherRunner:
    artifact: LoadedEventPaperArtifact
    target_date: date
    subscriber: PubSubSubscriber
    publisher: PubSubPublisher
    supabase: EventPaperSupabaseClient
    execution_candidate_id: str | None = None
    config: EventPaperPublishConfig = field(default_factory=EventPaperPublishConfig)
    wall_clock: WallClock = field(default_factory=lambda: lambda: datetime.now(UTC))
    sleep: Sleep = field(default=asyncio.sleep)

    async def run(self) -> EventPaperPublishReceipt:
        self.artifact.artifact.validate_target_date(self.target_date)
        candidates = self._selected_candidates()
        pending = {candidate.symbol: candidate for candidate in candidates}
        published: list[EventPaperPublishedRecord] = []
        skipped: dict[str, int] = {}
        recoverable: dict[str, tuple[EventPaperSignalClaim, StrategySignal]] = {}

        # Read durable state before applying the current entry window. A fully
        # checkpointed publication can reconstruct its receipt after a process
        # crash without touching Pub/Sub, even after the entry window closes.
        for symbol, candidate in list(pending.items()):
            signal_id = deterministic_strategy_signal_id(
                strategy_key=self.config.execution_strategy_key,
                candidate_id=candidate.execution_candidate_id,
                source=SignalSource.RULE,
                symbol=candidate.symbol,
                action=Action.BUY,
            )
            try:
                reasoning = await self.supabase.read_claim_reasoning(signal_id=signal_id)
            except EventPaperSupabaseError as exc:
                raise EventPaperPublishError(f"cannot read existing signal claim: {exc}") from exc
            if reasoning is None:
                continue
            try:
                claim, signal = signal_from_claim(
                    reasoning,
                    candidate=candidate,
                    artifact_sha256=self.artifact.sha256,
                )
            except (ValueError, ValidationError) as exc:
                raise EventPaperPublishError(
                    f"existing signal claim is incompatible: symbol={symbol}: {exc}"
                ) from exc
            if claim.publication is not None:
                published.append(self._record_from_checkpoint(claim=claim, signal=signal))
                pending.pop(symbol)
                continue
            if claim.publication_attempt is not None:
                published.append(self._record_from_attempt(claim=claim, signal=signal))
                pending.pop(symbol)
                continue
            recoverable[symbol] = (claim, signal)

        if not pending:
            return self._receipt(published=published, skipped=skipped)

        self._require_entry_window()
        try:
            await self.supabase.preflight(target_date=self.target_date)
        except EventPaperSupabaseError as exc:
            raise EventPaperPublishError(f"paper publish preflight failed: {exc}") from exc

        # A prior run may have durably selected a quote before any external
        # publication attempt. Publish that exact base claim without touching
        # the raw subscription; never choose a new price for the same ID.
        for symbol, (claim, signal) in recoverable.items():
            claim_rejection = self._claim_rejection(claim.raw_book_received_at)
            if claim_rejection is not None:
                raise EventPaperPublishError(
                    "existing selected quote cannot be replaced: "
                    f"symbol={symbol} reason={claim_rejection}"
                )
            published.append(
                await self._publish_claimed_signal(
                    claim=claim,
                    signal=signal,
                )
            )
            pending.pop(symbol)

        if not pending:
            return self._receipt(published=published, skipped=skipped)

        if self.config.seek_before_pull:
            try:
                await self.subscriber.seek(
                    self.config.subscription,
                    # Retain only the same bounded freshness window enforced
                    # below. Seeking to exact process start could discard the
                    # latest eligible quote for a quiet symbol.
                    target_time=self.wall_clock()
                    - timedelta(seconds=self.config.max_book_age_seconds),
                )
            except Exception as exc:
                raise EventPaperPublishError(
                    f"targeted seek failed for {self.config.subscription}: {exc}"
                ) from exc

        for _ in range(self.config.max_pull_batches):
            self._require_entry_window()
            try:
                messages = await self.subscriber.pull(
                    self.config.subscription,
                    max_messages=self.config.pull_max_messages,
                    return_immediately=True,
                )
            except Exception as exc:
                raise EventPaperPublishError(
                    f"raw book pull failed for {self.config.subscription}: {exc}"
                ) from exc
            if not messages:
                if self.config.idle_backoff_seconds:
                    await self.sleep(self.config.idle_backoff_seconds)
                continue
            for message in messages:
                outcome = await self._handle_message(
                    message,
                    pending=pending,
                    published=published,
                )
                if outcome is not None:
                    skipped[outcome] = skipped.get(outcome, 0) + 1
                if not pending:
                    return self._receipt(published=published, skipped=skipped)

        raise EventPaperPublishError(
            "entry publisher exhausted pull batches with unresolved candidates: "
            + ",".join(sorted(pending))
        )

    async def _handle_message(
        self,
        message: PulledMessage,
        *,
        pending: dict[str, EventPaperCandidate],
        published: list[EventPaperPublishedRecord],
    ) -> str | None:
        if message.attributes.get("kind") != "book":
            await self._ack(message)
            return "non_book"
        try:
            payload = json.loads(message.data.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("book payload is not an object")
            book = OrderBookSnapshot.model_validate(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, ValidationError):
            await self._ack(message)
            return "invalid_book"
        if message.attributes.get("symbol") not in {None, "", book.symbol}:
            await self._ack(message)
            return "attribute_symbol_mismatch"
        candidate = pending.get(book.symbol)
        if candidate is None:
            await self._ack(message)
            return "irrelevant_symbol"
        now = self.wall_clock()
        reason = book_rejection_reason(
            book=book,
            candidate=candidate,
            now=now,
            config=self.config,
        )
        if reason is not None:
            await self._ack(message)
            return reason

        try:
            await self.supabase.assert_paper_mode()
            _claim, proposed_signal = build_signal_claim(
                candidate=candidate,
                book=book,
                raw_book_message_id=message.message_id,
                artifact_sha256=self.artifact.sha256,
                config=self.config,
            )
            authoritative_reasoning = await self.supabase.claim_signal(proposed_signal)
            authoritative_claim, signal = signal_from_claim(
                authoritative_reasoning,
                candidate=candidate,
                artifact_sha256=self.artifact.sha256,
            )
        except (EventPaperSupabaseError, ValueError, ValidationError) as exc:
            # The target book stays unacked so a transient DB failure can be
            # retried with exactly the same quote. Business/preflight errors
            # abort the one-shot run and remain fail-closed.
            raise EventPaperPublishError(
                f"cannot claim event signal for symbol={book.symbol}: {exc}"
            ) from exc

        if authoritative_claim.publication is not None:
            await self._ack(message)
            published.append(self._record_from_checkpoint(claim=authoritative_claim, signal=signal))
            pending.pop(book.symbol)
            return None
        if authoritative_claim.publication_attempt is not None:
            await self._ack(message)
            published.append(self._record_from_attempt(claim=authoritative_claim, signal=signal))
            pending.pop(book.symbol)
            return None

        claim_rejection = self._claim_rejection(authoritative_claim.raw_book_received_at)
        if claim_rejection is not None:
            raise EventPaperPublishError(
                f"selected quote cannot publish: symbol={book.symbol} reason={claim_rejection}"
            )
        # Once the quote is durable, ack before the irreversible publish. An
        # ack or final-preflight failure still leaves the exact base claim
        # recoverable without falsely marking an external attempt.
        await self._assert_publish_ready(
            symbol=book.symbol,
            book_received_at=authoritative_claim.raw_book_received_at,
        )
        await self._ack(message)
        published.append(
            await self._publish_claimed_signal(
                claim=authoritative_claim,
                signal=signal,
            )
        )
        pending.pop(book.symbol)
        return None

    async def _publish_claimed_signal(
        self,
        *,
        claim: EventPaperSignalClaim,
        signal: StrategySignal,
    ) -> EventPaperPublishedRecord:
        if claim.publication_attempt is not None or claim.publication is not None:
            raise EventPaperPublishError("strategy signal claim already has publication progress")

        rpc_started = False
        attempted_claim: EventPaperSignalClaim | None = None

        async def _before_attempt() -> None:
            nonlocal attempted_claim, rpc_started
            if rpc_started:
                # A transport error after the request starts is ambiguous: the
                # broker may already have accepted it. Never issue a second
                # external publish for the same durable attempt.
                raise EventPaperPublishError(
                    "strategy signal publish result is ambiguous; automatic retry disabled: "
                    f"symbol={signal.symbol}"
                )
            await self._assert_publish_ready(
                symbol=signal.symbol,
                book_received_at=claim.raw_book_received_at,
            )
            attempted_claim = await self._begin_publication_attempt(
                claim=claim,
                signal=signal,
            )
            if attempted_claim.publication is not None:
                raise _PublicationAlreadyResolved(attempted_claim)
            rpc_started = True

        try:
            message_id = await self.publisher.publish(
                self.config.signal_topic,
                data=signal.model_dump_json().encode("utf-8"),
                attributes={
                    "symbol": signal.symbol,
                    "source": signal.source.value,
                    "routing_intent": signal.routing_intent.value,
                    "strategy_key": signal.strategy_key or "",
                    "candidate_id": signal.candidate_id or "",
                    "mode": "paper",
                },
                before_attempt=_before_attempt,
                disable_internal_retry=True,
            )
        except _PublicationAlreadyResolved as exc:
            return self._record_from_checkpoint(claim=exc.claim, signal=signal)
        except EventPaperPublishError:
            raise
        except Exception as exc:
            raise EventPaperPublishError(
                f"strategy signal publish failed: symbol={signal.symbol}: {exc}"
            ) from exc
        if attempted_claim is None or attempted_claim.publication_attempt is None:
            raise EventPaperPublishError("durable publication attempt is missing after publish")
        published_at = self.wall_clock()
        try:
            checkpointed = await self.supabase.checkpoint_publication(
                signal_id=signal.signal_id,
                claim=attempted_claim,
                strategy_message_id=message_id,
                published_at=published_at,
            )
        except EventPaperSupabaseError as exc:
            raise EventPaperPublishError(
                f"strategy signal published but checkpoint failed: symbol={signal.symbol}: {exc}"
            ) from exc
        return self._record_from_checkpoint(claim=checkpointed, signal=signal)

    async def _begin_publication_attempt(
        self,
        *,
        claim: EventPaperSignalClaim,
        signal: StrategySignal,
    ) -> EventPaperSignalClaim:
        attempt_id = str(uuid4())
        try:
            attempted = await self.supabase.begin_publication_attempt(
                signal_id=signal.signal_id,
                claim=claim,
                attempt_id=attempt_id,
                attempted_at=self.wall_clock(),
            )
        except EventPaperSupabaseError as exc:
            raise EventPaperPublishError(
                f"cannot persist publication attempt: symbol={signal.symbol}: {exc}"
            ) from exc
        if attempted.publication is not None:
            return attempted
        if attempted.publication_attempt is None:
            raise EventPaperPublishError("durable publication attempt is missing")
        if attempted.publication_attempt.attempt_id != attempt_id:
            raise EventPaperPublishError(
                f"publication attempt is owned by another invocation: symbol={signal.symbol}"
            )
        return attempted

    def _record_from_checkpoint(
        self,
        *,
        claim: EventPaperSignalClaim,
        signal: StrategySignal,
    ) -> EventPaperPublishedRecord:
        publication = claim.publication
        attempt = claim.publication_attempt
        if publication is None or attempt is None:
            raise EventPaperPublishError("strategy signal publication checkpoint is missing")
        return EventPaperPublishedRecord(
            strategy_key=signal.strategy_key,
            execution_candidate_id=signal.candidate_id,
            symbol=signal.symbol,
            signal_id=str(signal.signal_id),
            raw_book_message_id=claim.raw_book_message_id,
            observed_ask=signal.price,
            book_received_at=claim.raw_book_received_at,
            publication_status="confirmed",
            publication_attempt_id=publication.attempt_id,
            attempted_at=attempt.attempted_at,
            strategy_message_id=publication.strategy_message_id,
            topic=publication.topic,
            published_at=publication.published_at,
            artifact_sha256=self.artifact.sha256,
        )

    def _record_from_attempt(
        self,
        *,
        claim: EventPaperSignalClaim,
        signal: StrategySignal,
    ) -> EventPaperPublishedRecord:
        attempt = claim.publication_attempt
        if attempt is None or claim.publication is not None:
            raise EventPaperPublishError("ambiguous publication attempt state is invalid")
        return EventPaperPublishedRecord(
            strategy_key=signal.strategy_key,
            execution_candidate_id=signal.candidate_id,
            symbol=signal.symbol,
            signal_id=str(signal.signal_id),
            raw_book_message_id=claim.raw_book_message_id,
            observed_ask=signal.price,
            book_received_at=claim.raw_book_received_at,
            publication_status="ambiguous",
            publication_attempt_id=attempt.attempt_id,
            attempted_at=attempt.attempted_at,
            strategy_message_id=None,
            topic=self.config.signal_topic,
            published_at=None,
            artifact_sha256=self.artifact.sha256,
        )

    async def _assert_publish_ready(
        self,
        *,
        symbol: str,
        book_received_at: datetime,
    ) -> None:
        try:
            await self.supabase.assert_entry_ready(target_date=self.target_date)
        except EventPaperSupabaseError as exc:
            raise EventPaperPublishError(
                f"final entry preflight failed: symbol={symbol}: {exc}"
            ) from exc
        # The initial preflight, claim write, or an internal Pub/Sub retry may
        # cross a time boundary. Recheck immediately before every attempt.
        self._require_entry_window()
        claim_rejection = self._claim_rejection(book_received_at)
        if claim_rejection is not None:
            raise EventPaperPublishError(
                f"selected quote cannot publish: symbol={symbol} reason={claim_rejection}"
            )

    async def _ack(self, message: PulledMessage) -> None:
        try:
            await self.subscriber.acknowledge(self.config.subscription, [message.ack_id])
        except Exception as exc:
            raise EventPaperPublishError(
                f"raw book acknowledge failed: message_id={message.message_id}: {exc}"
            ) from exc

    def _claim_rejection(self, received_at: datetime) -> str | None:
        if received_at.tzinfo is None:
            return "naive_received_at"
        now = self.wall_clock()
        if now.tzinfo is None:
            return "naive_wall_clock"
        # Date comparison uses the publisher's explicit target date; exact JST
        # window validation was already applied to the selected book claim.
        if received_at.astimezone(JST).date() != self.target_date:
            return "wrong_book_date"
        local_time = received_at.astimezone(JST).time().replace(tzinfo=None)
        if local_time < self.config.entry_window_start:
            return "book_before_entry_window"
        if local_time >= self.config.entry_window_end:
            return "book_after_entry_window"
        age = (now - received_at).total_seconds()
        if age < -self.config.max_future_skew_seconds:
            return "future_book"
        if age > self.config.max_book_age_seconds:
            return "stale_book"
        return None

    def _require_entry_window(self) -> None:
        reason = entry_window_rejection(
            now=self.wall_clock(),
            target_date=self.target_date,
            config=self.config,
        )
        if reason is not None:
            raise EventPaperPublishError(f"event entry window rejected: {reason}")

    def _receipt(
        self,
        *,
        published: list[EventPaperPublishedRecord],
        skipped: dict[str, int],
    ) -> EventPaperPublishReceipt:
        return EventPaperPublishReceipt(
            execution_profile=self.config.execution_profile,
            target_date=self.target_date,
            artifact_path=str(self.artifact.source_path),
            artifact_sha256=self.artifact.sha256,
            selected_execution_candidate_ids=[
                record.execution_candidate_id for record in published
            ],
            published=published,
            skipped_messages=skipped,
        )

    def _selected_candidates(self) -> list[EventPaperCandidate]:
        candidates = self.artifact.artifact.candidates
        if self.execution_candidate_id is None:
            if len(candidates) != 1:
                raise EventPaperPublishError(
                    "exactly one event occurrence must be selected per invocation; "
                    "set --execution-candidate-id for a multi-candidate artifact"
                )
            return [candidates[0]]
        selected = [
            candidate
            for candidate in candidates
            if candidate.execution_candidate_id == self.execution_candidate_id
        ]
        if len(selected) != 1:
            raise EventPaperPublishError(
                "selected execution_candidate_id is missing or duplicated in the artifact"
            )
        return selected
