"""Feeder のメインループ (Phase 2 段階)。

責務:
- watchlist スナップショットを ``WatchlistFeed`` から受け取り、registry の
  差分から ``/register`` / ``/unregister`` を呼ぶ
- ``connect_websocket`` で WS 接続を維持し、PUSH メッセージを ``parser`` で
  ``TickData`` / ``OrderBookSnapshot`` に変換して ``sink`` に流す
- ``watchlist`` 更新タスクと WS メッセージ受信タスクは並行に動かし、片方が
  落ちたらもう片方をキャンセルして全体を作り直す (再接続)
- 接続失敗・切断は ``BackoffPolicy`` に従って指数バックオフ後にやり直す
- 401/403 はトークン失効として ``invalidate_token`` を呼んで再発行
- 起動時 / 再接続時には ``unregister_all`` で kabu 側状態を初期化してから
  watchlist を再 register する (kabu の register 状態保持仕様が未確認のため)

Phase 2 では Pub/Sub publish はせず、``MessageSink`` プロトコルでフックだけ
用意する (デフォルトは JSON Lines を ``stdout`` に書く ``stdout_sink``)。
Phase 3 で Pub/Sub publisher を sink に差し替える。

外側 (CLI) からはまず ``run_session(...)`` を呼ぶ。``iterations`` を渡すと
テストから有限回で抜けられる。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager, AsyncExitStack, suppress
from dataclasses import dataclass, field
from typing import Any, Protocol

from trade_contracts.market import OrderBookSnapshot, TickData
from websockets.exceptions import ConnectionClosed

from ..kabu_client import KabuApiError, SymbolRegistration
from ..parser import parse_push_message
from .reconnect import BackoffPolicy
from .registry import RegistrationDiff, compute_diff

logger = logging.getLogger(__name__)


WatchlistFeed = AsyncIterator[list[Any]]
"""Phase 2 では ``WatchlistRow`` のリストだが、抽象として list[Any] で受ける。"""


class MessageSink(Protocol):
    """``TickData`` / ``OrderBookSnapshot`` の出力先。Phase 3 で publisher 差し替え。"""

    async def __call__(self, record: TickData | OrderBookSnapshot) -> None: ...


class KabuStreamingClient(Protocol):
    """``FeederSession`` が要求する kabu API の最小インタフェース。

    本番では ``feeder.kabu_client.KabuClient`` がそのまま満たす。テストでは
    in-memory の fake を渡せるよう Protocol 化してある。
    """

    async def ensure_token(self) -> str: ...
    async def register(self, symbols: Sequence[SymbolRegistration]) -> dict[str, Any]: ...
    async def unregister(self, symbols: Sequence[SymbolRegistration]) -> dict[str, Any]: ...
    async def unregister_all(self) -> dict[str, Any]: ...
    def invalidate_token(self) -> None: ...
    def connect_websocket(
        self,
    ) -> AbstractAsyncContextManager[AsyncIterable[str | bytes]]: ...


def stdout_sink_factory() -> MessageSink:
    """Phase 2 用デフォルト sink。1 レコード = JSON 1 行で stdout に書く。"""

    async def _sink(record: TickData | OrderBookSnapshot) -> None:
        kind = "tick" if isinstance(record, TickData) else "book"
        line = {"kind": kind, "data": json.loads(record.model_dump_json())}
        sys.stdout.write(json.dumps(line, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    return _sink


@dataclass(frozen=True, slots=True)
class CycleStats:
    """1 サイクル ((re)connect → 切断 までの集計)。テストとログ用。"""

    registered: int
    unregistered: int
    messages_received: int
    records_emitted: int
    ended_reason: str


@dataclass(slots=True)
class FeederSession:
    """Feeder のストリーミングセッション。

    使い方::

        async with KabuClient(...) as kabu, SupabaseWatchlistReader(...) as reader:
            feed = PollingWatchlistFeed(reader=reader, ...)
            session = FeederSession(
                kabu=kabu, watchlist_feed=feed.subscribe(),
                default_exchange=1, sink=stdout_sink_factory(),
            )
            await session.run()

    ``run`` は永続ループ。``iterations`` を渡すと有限回で抜ける (テスト用)。
    """

    kabu: KabuStreamingClient
    watchlist_feed: WatchlistFeed
    default_exchange: int
    sink: MessageSink
    backoff: BackoffPolicy = field(default_factory=BackoffPolicy)
    sleep: Callable[[float], Awaitable[None]] = field(default=asyncio.sleep)
    max_pending_sends: int = 64
    _registered: set[SymbolRegistration] = field(default_factory=set, init=False)

    async def run(self, *, iterations: int | None = None) -> list[CycleStats]:
        """セッションループ本体。``iterations`` を渡すと有限回で停止する。"""
        results: list[CycleStats] = []
        attempt = 0
        count = 0
        while iterations is None or count < iterations:
            try:
                stats = await self._run_one_cycle()
                results.append(stats)
                attempt = 0  # 正常終了 (= 切断検知) でリセット
            except KabuApiError as exc:
                if exc.status_code in (401, 403):
                    logger.warning("auth lost (%s); invalidating token", exc.status_code)
                    self.kabu.invalidate_token()
                    self._registered.clear()
                else:
                    logger.exception("kabu API error during cycle")
                attempt += 1
                results.append(
                    CycleStats(0, 0, 0, 0, ended_reason=f"kabu_api_error_{exc.status_code}")
                )
            except (TimeoutError, OSError):
                logger.exception("connection error during cycle")
                attempt += 1
                results.append(CycleStats(0, 0, 0, 0, ended_reason="connection_error"))
            except StopAsyncIteration:
                logger.info("watchlist feed exhausted; ending session")
                results.append(CycleStats(0, 0, 0, 0, ended_reason="watchlist_exhausted"))
                break

            count += 1
            if iterations is not None and count >= iterations:
                break
            if attempt > 0:
                wait = self.backoff.compute(attempt)
                logger.info("reconnect backoff: attempt=%d wait=%.2fs", attempt, wait)
                await self.sleep(wait)
        return results

    async def _run_one_cycle(self) -> CycleStats:
        """1 サイクル: token → unregister_all → 初回 register → WS 接続 → 受信ループ。

        watchlist の追加/削除を反映するため、watchlist 監視タスクと WS 受信
        タスクを並行に動かす。どちらか一方が抜けた / 例外を出した時点で
        サイクル終了。残りタスクはキャンセルして次サイクルへ。
        """
        await self.kabu.ensure_token()
        # 起動時/再接続時は kabu 側状態が不明なので一旦クリア
        try:
            await self.kabu.unregister_all()
        except KabuApiError as exc:
            # unregister_all が失敗しても致命ではない。register でリカバリ可能
            logger.warning("unregister_all failed: %s", exc)
        self._registered.clear()

        # 最初の watchlist スナップショットを取り、初回 register を済ませてから WS 接続
        first_rows = await self._next_watchlist_snapshot()
        diff = self._diff_against_current(first_rows)
        applied = await self._apply_diff(diff)
        registered_total = applied.add_count
        unregistered_total = applied.remove_count

        counters = _Counters()
        ended_reason = "unknown"
        async with AsyncExitStack() as stack:
            ws = await stack.enter_async_context(self.kabu.connect_websocket())
            ws_task = asyncio.create_task(self._consume_ws(ws, counters), name="feeder-ws")
            wl_task = asyncio.create_task(
                self._consume_watchlist(counters), name="feeder-watchlist"
            )
            done, pending = await asyncio.wait(
                {ws_task, wl_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            for task in pending:
                with suppress(asyncio.CancelledError, Exception):
                    await task

            first_done = next(iter(done))
            task_exc = first_done.exception()
            if task_exc is not None:
                raise task_exc
            ended_reason = "ws_closed" if first_done is ws_task else "watchlist_exhausted"

        return CycleStats(
            registered=registered_total + counters.registered,
            unregistered=unregistered_total + counters.unregistered,
            messages_received=counters.messages_received,
            records_emitted=counters.records_emitted,
            ended_reason=ended_reason,
        )

    async def _consume_ws(self, ws: AsyncIterable[str | bytes], counters: _Counters) -> None:
        pending_sends: set[asyncio.Task[None]] = set()
        try:
            async for raw in ws:
                counters.messages_received += 1
                payload = _decode_json(raw)
                if payload is None:
                    continue
                for record in parse_push_message(payload):
                    await self._enqueue_sink_send(record, pending_sends, counters)
                await self._drain_completed_sends(pending_sends, counters)
            await self._finish_pending_sends(pending_sends, counters)
        except ConnectionClosed as exc:
            await self._cancel_pending_sends(pending_sends)
            raise OSError(f"websocket closed: {exc}") from exc
        except Exception:
            await self._cancel_pending_sends(pending_sends)
            raise

    async def _consume_watchlist(self, counters: _Counters) -> None:
        """初回以降の watchlist スナップショットを取り、差分を反映し続ける。"""
        async for rows in self.watchlist_feed:
            diff = self._diff_against_current(rows)
            if diff.is_empty:
                continue
            applied = await self._apply_diff(diff)
            counters.registered += applied.add_count
            counters.unregistered += applied.remove_count

    async def _next_watchlist_snapshot(self) -> list[Any]:
        """``watchlist_feed`` から 1 件取り出す。空ストリームなら ``StopAsyncIteration``。"""
        return await self.watchlist_feed.__anext__()

    def _diff_against_current(self, rows: list[Any]) -> RegistrationDiff:
        desired = frozenset(
            SymbolRegistration(symbol=row.symbol, exchange=self.default_exchange)
            for row in rows
            if getattr(row, "symbol", None)
        )
        return compute_diff(current=self._registered, desired=desired)

    async def _apply_diff(self, diff: RegistrationDiff) -> _ApplyResult:
        if diff.remove:
            await self.kabu.unregister(list(diff.remove))
            self._registered.difference_update(diff.remove)
        if diff.add:
            await self.kabu.register(list(diff.add))
            self._registered.update(diff.add)
        return _ApplyResult(add_count=len(diff.add), remove_count=len(diff.remove))

    async def _enqueue_sink_send(
        self,
        record: TickData | OrderBookSnapshot,
        pending_sends: set[asyncio.Task[None]],
        counters: _Counters,
    ) -> None:
        task = asyncio.create_task(self.sink(record), name="feeder-sink-send")
        pending_sends.add(task)
        if len(pending_sends) >= max(1, self.max_pending_sends):
            await self._collect_send_results(pending_sends, counters, wait_for_one=True)

    async def _drain_completed_sends(
        self,
        pending_sends: set[asyncio.Task[None]],
        counters: _Counters,
    ) -> None:
        await self._collect_send_results(pending_sends, counters, wait_for_one=False)

    async def _finish_pending_sends(
        self,
        pending_sends: set[asyncio.Task[None]],
        counters: _Counters,
    ) -> None:
        while pending_sends:
            await self._collect_send_results(pending_sends, counters, wait_for_one=True)

    async def _collect_send_results(
        self,
        pending_sends: set[asyncio.Task[None]],
        counters: _Counters,
        *,
        wait_for_one: bool,
    ) -> None:
        if not pending_sends:
            return
        if wait_for_one:
            done, _ = await asyncio.wait(
                pending_sends,
                return_when=asyncio.FIRST_COMPLETED,
            )
        else:
            done = {task for task in pending_sends if task.done()}
            if not done:
                return
        pending_sends.difference_update(done)
        for task in done:
            exc = task.exception()
            if exc is not None:
                await self._cancel_pending_sends(pending_sends)
                raise exc
            counters.records_emitted += 1

    async def _cancel_pending_sends(self, pending_sends: set[asyncio.Task[None]]) -> None:
        if not pending_sends:
            return
        for task in pending_sends:
            task.cancel()
        for task in list(pending_sends):
            with suppress(asyncio.CancelledError, Exception):
                await task
        pending_sends.clear()


@dataclass(slots=True)
class _ApplyResult:
    add_count: int
    remove_count: int


@dataclass(slots=True)
class _Counters:
    registered: int = 0
    unregistered: int = 0
    messages_received: int = 0
    records_emitted: int = 0


def _decode_json(raw: str | bytes) -> dict[str, Any] | None:
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            logger.warning("ws message decode failed: not utf-8")
            return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("ws message is not valid json: %r", raw[:200])
        return None
    if not isinstance(payload, dict):
        logger.warning("ws message is not object json: type=%s", type(payload).__name__)
        return None
    return payload
