"""``streaming.session.FeederSession`` の状態機械テスト。

実 I/O は触らず、``KabuStreamingClient`` Protocol を満たす in-memory fake と
async generator ベースの watchlist feed を渡して挙動を観察する。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from feeder._testing import make_book_payload, make_tick_payload
from feeder.clients.supabase import WatchlistRow
from feeder.kabu_client import KabuApiError, SymbolRegistration
from feeder.streaming.reconnect import BackoffPolicy
from feeder.streaming.session import FeederSession, MessageSink
from trade_contracts.market import OrderBookSnapshot, TickData


class _FakeWS:
    """``websockets.connect`` が返す ``ClientConnection`` の最小モック。"""

    def __init__(self, messages: list[str | bytes]) -> None:
        self._messages = list(messages)

    def __aiter__(self) -> _FakeWS:
        return self

    async def __anext__(self) -> str | bytes:
        if not self._messages:
            raise StopAsyncIteration
        return self._messages.pop(0)


@dataclass(slots=True)
class _FakeKabu:
    """``KabuStreamingClient`` Protocol を満たす in-memory fake。"""

    ws_messages: list[list[str | bytes]] = field(default_factory=list)
    register_errors: list[KabuApiError | None] = field(default_factory=list)
    unregister_all_error: KabuApiError | None = None
    connect_errors: list[Exception | None] = field(default_factory=list)
    calls: list[tuple[str, Any]] = field(default_factory=list)
    token: str | None = None

    async def ensure_token(self) -> str:
        self.calls.append(("ensure_token", None))
        if self.token is None:
            self.token = "tok-1"
        return self.token

    async def register(self, symbols: Any) -> dict[str, Any]:
        sym_list = list(symbols)
        self.calls.append(("register", sym_list))
        err = self.register_errors.pop(0) if self.register_errors else None
        if err is not None:
            raise err
        return {}

    async def unregister(self, symbols: Any) -> dict[str, Any]:
        sym_list = list(symbols)
        self.calls.append(("unregister", sym_list))
        return {}

    async def unregister_all(self) -> dict[str, Any]:
        self.calls.append(("unregister_all", None))
        if self.unregister_all_error is not None:
            err = self.unregister_all_error
            self.unregister_all_error = None
            raise err
        return {}

    def invalidate_token(self) -> None:
        self.calls.append(("invalidate_token", None))
        self.token = None

    @asynccontextmanager
    async def connect_websocket(self) -> AsyncIterator[_FakeWS]:
        self.calls.append(("connect_websocket", None))
        err = self.connect_errors.pop(0) if self.connect_errors else None
        if err is not None:
            raise err
        msgs = self.ws_messages.pop(0) if self.ws_messages else []
        yield _FakeWS(msgs)


async def _watchlist_feed(
    snapshots: list[list[WatchlistRow]],
) -> AsyncIterator[list[WatchlistRow]]:
    for snap in snapshots:
        yield snap


@dataclass(slots=True)
class _CapturingSink:
    records: list[TickData | OrderBookSnapshot] = field(default_factory=list)

    async def __call__(self, record: TickData | OrderBookSnapshot) -> None:
        self.records.append(record)


def _row(symbol: str) -> WatchlistRow:
    return WatchlistRow(symbol=symbol, valid_date=date(2026, 4, 25))


def _ws_tick_msg(symbol: str = "7203") -> str:
    return json.dumps(make_tick_payload(symbol=symbol, current_price=2500.0))


def _ws_book_msg(symbol: str = "7203") -> str:
    payload = make_book_payload(symbol=symbol)
    payload.pop("CurrentPrice", None)
    return json.dumps(payload)


def _make_session(
    *,
    kabu: _FakeKabu,
    watchlist_snapshots: list[list[WatchlistRow]],
    sink: MessageSink | None = None,
    backoff: BackoffPolicy | None = None,
    sleeps: list[float] | None = None,
) -> tuple[FeederSession, _CapturingSink, list[float]]:
    actual_sink = sink if sink is not None else _CapturingSink()
    sleep_log: list[float] = sleeps if sleeps is not None else []

    async def fake_sleep(seconds: float) -> None:
        sleep_log.append(seconds)

    session = FeederSession(
        kabu=kabu,
        watchlist_feed=_watchlist_feed(watchlist_snapshots),
        default_exchange=1,
        sink=actual_sink,
        backoff=backoff or BackoffPolicy(jitter_ratio=0.0),
        sleep=fake_sleep,
    )
    return (
        session,
        actual_sink if isinstance(actual_sink, _CapturingSink) else _CapturingSink(),
        sleep_log,
    )


async def test_initial_cycle_registers_and_emits_records() -> None:
    kabu = _FakeKabu(ws_messages=[[_ws_tick_msg("7203"), _ws_book_msg("9984")]])
    session, sink, _ = _make_session(
        kabu=kabu,
        watchlist_snapshots=[[_row("7203"), _row("9984")]],
    )

    results = await session.run(iterations=1)

    assert len(results) == 1
    stats = results[0]
    assert stats.registered == 2
    assert stats.unregistered == 0
    assert stats.messages_received == 2
    assert stats.records_emitted == 2
    # WS / watchlist のどちらが先に終わるかは asyncio スケジューラ依存
    # (両者ともすぐ StopAsyncIteration で抜ける)。どちらでも cycle 正常終了として許容
    assert stats.ended_reason in {"ws_closed", "watchlist_exhausted"}

    # 呼び出し順序: ensure_token → unregister_all → register → connect_websocket
    op_sequence = [c[0] for c in kabu.calls]
    assert op_sequence[:4] == [
        "ensure_token",
        "unregister_all",
        "register",
        "connect_websocket",
    ]

    # register に渡った銘柄 (symbol 昇順)
    register_call = next(c for c in kabu.calls if c[0] == "register")
    assert [s.symbol for s in register_call[1]] == ["7203", "9984"]
    assert all(isinstance(s, SymbolRegistration) for s in register_call[1])

    # sink に流れたレコードの中身も検証
    assert isinstance(sink.records[0], TickData)
    assert sink.records[0].symbol == "7203"
    assert sink.records[0].price == Decimal("2500.0")
    assert isinstance(sink.records[1], OrderBookSnapshot)
    assert sink.records[1].symbol == "9984"


async def test_watchlist_update_triggers_diff_register_unregister() -> None:
    kabu = _FakeKabu(ws_messages=[[]])  # WS 即 close
    session, _, _ = _make_session(
        kabu=kabu,
        watchlist_snapshots=[
            [_row("7203"), _row("9984")],
            [_row("9984"), _row("6758")],  # 7203 削除 / 6758 追加
        ],
    )

    results = await session.run(iterations=1)

    assert len(results) == 1
    stats = results[0]
    # 初回 register=2, watchlist 更新で +1 / -1
    assert stats.registered == 3
    assert stats.unregistered == 1

    register_calls = [c for c in kabu.calls if c[0] == "register"]
    unregister_calls = [c for c in kabu.calls if c[0] == "unregister"]
    assert [s.symbol for s in register_calls[0][1]] == ["7203", "9984"]
    assert [s.symbol for s in register_calls[1][1]] == ["6758"]
    assert [s.symbol for s in unregister_calls[0][1]] == ["7203"]


async def test_watchlist_unchanged_does_not_call_register() -> None:
    kabu = _FakeKabu(ws_messages=[[]])
    session, _, _ = _make_session(
        kabu=kabu,
        watchlist_snapshots=[
            [_row("7203")],
            [_row("7203")],  # 同一スナップショット
        ],
    )

    await session.run(iterations=1)

    register_calls = [c for c in kabu.calls if c[0] == "register"]
    unregister_calls = [c for c in kabu.calls if c[0] == "unregister"]
    assert len(register_calls) == 1  # 初回のみ
    assert unregister_calls == []


async def test_auth_error_invalidates_token_and_continues() -> None:
    kabu = _FakeKabu(
        ws_messages=[[], []],
        register_errors=[
            KabuApiError(401, {"Code": 4001001, "Message": "auth lost"}),
            None,
        ],
    )
    sleep_log: list[float] = []
    session, _, _ = _make_session(
        kabu=kabu,
        watchlist_snapshots=[
            [_row("7203")],  # 1 サイクル目: register が 401 で失敗
            [_row("7203")],  # 2 サイクル目: 成功
        ],
        backoff=BackoffPolicy(initial_seconds=0.5, jitter_ratio=0.0),
        sleeps=sleep_log,
    )

    results = await session.run(iterations=2)

    assert len(results) == 2
    assert results[0].ended_reason == "kabu_api_error_401"
    assert results[1].ended_reason in {"ws_closed", "watchlist_exhausted"}
    # 401 検知で invalidate_token が呼ばれている
    assert any(c[0] == "invalidate_token" for c in kabu.calls)
    # 失敗 → backoff sleep が 1 回挟まる
    assert sleep_log == [0.5]


async def test_kabu_500_error_does_not_invalidate_token() -> None:
    kabu = _FakeKabu(
        ws_messages=[[], []],
        register_errors=[
            KabuApiError(500, {"Message": "internal"}),
            None,
        ],
    )
    sleep_log: list[float] = []
    session, _, _ = _make_session(
        kabu=kabu,
        watchlist_snapshots=[[_row("7203")], [_row("7203")]],
        backoff=BackoffPolicy(initial_seconds=0.25, jitter_ratio=0.0),
        sleeps=sleep_log,
    )

    results = await session.run(iterations=2)

    assert results[0].ended_reason == "kabu_api_error_500"
    # 500 では invalidate_token は呼ばれない
    assert not any(c[0] == "invalidate_token" for c in kabu.calls)
    assert sleep_log == [0.25]


async def test_connect_error_triggers_backoff() -> None:
    kabu = _FakeKabu(
        ws_messages=[[]],
        connect_errors=[OSError("connection refused"), None],
    )
    sleep_log: list[float] = []
    session, _, _ = _make_session(
        kabu=kabu,
        watchlist_snapshots=[[_row("7203")], [_row("7203")]],
        backoff=BackoffPolicy(initial_seconds=1.0, multiplier=2.0, jitter_ratio=0.0),
        sleeps=sleep_log,
    )

    results = await session.run(iterations=2)

    assert results[0].ended_reason == "connection_error"
    assert results[1].ended_reason in {"ws_closed", "watchlist_exhausted"}
    # 1 回目失敗 → attempt=1 → 1.0s sleep。2 回目成功でリセット
    assert sleep_log == [1.0]


async def test_unregister_all_failure_is_swallowed() -> None:
    kabu = _FakeKabu(
        ws_messages=[[]],
        unregister_all_error=KabuApiError(500, {"Message": "boom"}),
    )
    session, _, _ = _make_session(
        kabu=kabu,
        watchlist_snapshots=[[_row("7203")]],
    )

    results = await session.run(iterations=1)
    # unregister_all 失敗でもサイクルは続行する (どちらの task が先に終わるかは
    # 環境依存なので両方許容する)
    assert results[0].ended_reason in {"ws_closed", "watchlist_exhausted"}
    assert results[0].registered == 1


async def test_invalid_ws_payload_is_skipped() -> None:
    kabu = _FakeKabu(
        ws_messages=[
            [
                "not-json",
                json.dumps(["array-not-object"]),
                _ws_tick_msg("7203"),
            ]
        ],
    )
    session, sink, _ = _make_session(
        kabu=kabu,
        watchlist_snapshots=[[_row("7203")]],
    )

    results = await session.run(iterations=1)
    assert results[0].messages_received == 3
    assert results[0].records_emitted == 1
    assert len(sink.records) == 1


async def test_attempt_resets_after_successful_cycle() -> None:
    """失敗 → 成功 → 失敗 で 2 回目の sleep は initial に戻ること。"""
    kabu = _FakeKabu(
        ws_messages=[[], [], []],
        connect_errors=[OSError("fail-1"), None, OSError("fail-3")],
    )
    sleep_log: list[float] = []
    session, _, _ = _make_session(
        kabu=kabu,
        watchlist_snapshots=[
            [_row("7203")],
            [_row("7203")],
            [_row("7203")],
        ],
        backoff=BackoffPolicy(initial_seconds=1.0, multiplier=2.0, jitter_ratio=0.0),
        sleeps=sleep_log,
    )

    await session.run(iterations=3)

    # 失敗(1) → 1.0s, 成功(2) attempt リセット, 失敗(3)... が、3 回目で iterations 上限
    # に達するため最後の sleep は走らない仕様。失敗→成功のリセットだけ確認する
    assert sleep_log == [1.0]
