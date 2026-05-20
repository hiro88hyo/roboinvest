"""Phase 3: Pub/Sub publisher を組み込んだストリーミングランナー。

役割:
- ``PubSubPublishSink`` — ``FeederSession`` の ``MessageSink`` を満たす実装。
  ``raw-market-data`` トピックへ ``TickData`` / ``OrderBookSnapshot`` の JSON
  をそのまま publish する (feature-engine / oms-paper は ``bids``/``asks``
  フィールドの有無で型判別するため、wrapper は付けない)
- ``run_stream(...)`` — ``KabuClient`` + ``SupabaseWatchlistReader`` +
  ``PubSubPublisher`` を束ねて ``FeederSession`` を回すエントリ関数。CLI から
  ``--iterations`` を渡せばテスト可能
- ``run_replay(...)`` — 録画した PUSH JSONL を 1 行ずつパースして
  ``parse_push_message`` → publish する再生モード。kabuステーションへの
  接続なしで feature-engine 側の結合確認に使える

publish 失敗時は ``PubSubError`` を ``OSError`` に詰め替えて投げ、session の
既存の reconnect ループ (``OSError`` をハンドル済み) に乗せる。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from trade_contracts.kabu_token import KabuTokenCache
from trade_contracts.market import OrderBookSnapshot, TickData

from ..clients.pubsub import PubSubError, PubSubPublisher
from ..clients.supabase import PollingWatchlistFeed, SupabaseWatchlistReader
from ..config import FeederSettings
from ..kabu_client import KabuClient
from ..parser import parse_push_message
from .reconnect import BackoffPolicy
from .session import CycleStats, FeederSession, MessageSink

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PubSubPublishSink:
    """``FeederSession.MessageSink`` を満たす Pub/Sub publisher。

    1 レコード = 1 publish。tenacity で 3 回 retry した上で失敗した場合は、
    session の reconnect ロジックに乗せるため ``OSError`` で再送出する
    (session は ``OSError`` を「接続系の一時障害」として backoff 後に WS を
    張り直す)。
    """

    publisher: PubSubPublisher
    topic: str

    async def __call__(self, record: TickData | OrderBookSnapshot) -> None:
        kind = "tick" if isinstance(record, TickData) else "book"
        data = record.model_dump_json().encode("utf-8")
        try:
            await self.publisher.publish(
                self.topic,
                data=data,
                attributes={"symbol": record.symbol, "kind": kind},
            )
        except PubSubError as exc:
            # 短時間 retry 後に WS 再接続させる方針。OSError に詰め替えて
            # session.run の except (TimeoutError, OSError) に乗せる。
            logger.exception(
                "publish failed after retries: topic=%s symbol=%s kind=%s",
                self.topic,
                record.symbol,
                kind,
            )
            raise OSError(f"pubsub publish failed: {exc}") from exc


def build_publish_sink(publisher: PubSubPublisher, *, topic: str) -> MessageSink:
    """``PubSubPublishSink`` を ``MessageSink`` として返す薄いファクトリ。"""
    return PubSubPublishSink(publisher=publisher, topic=topic)


async def run_stream(
    settings: FeederSettings,
    *,
    iterations: int | None = None,
) -> list[CycleStats]:
    """``stream`` モードの本体。常駐するときは ``iterations=None``。

    KabuClient / SupabaseWatchlistReader / PubSubPublisher を構築し、
    FeederSession を回す。例外は呼び出し元 (CLI) に伝播する。
    """
    if not settings.kabu_api_password:
        raise RuntimeError("KABU_API_PASSWORD must be set for stream mode")
    if not settings.supabase_url or not settings.supabase_secret_key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SECRET_KEY must be set for stream mode")
    if not settings.pubsub_project_id:
        raise RuntimeError("PUBSUB_PROJECT_ID must be set for stream mode")

    backoff = BackoffPolicy(
        initial_seconds=settings.reconnect_initial_backoff_sec,
        max_seconds=settings.reconnect_max_backoff_sec,
    )

    token_cache = (
        KabuTokenCache(Path(settings.kabu_token_cache_file))
        if settings.kabu_token_cache_file
        else None
    )
    async with (
        KabuClient(
            base_url=settings.kabu_api_base_url,
            api_password=settings.kabu_api_password,
            ws_url=settings.kabu_ws_url,
            timeout_seconds=settings.kabu_http_timeout_seconds,
            ws_ping_interval=settings.kabu_ws_ping_interval_seconds,
            ws_ping_timeout=settings.kabu_ws_ping_timeout_seconds,
            ws_max_queue=settings.kabu_ws_max_queue,
            token_cache=token_cache,
        ) as kabu,
        SupabaseWatchlistReader(
            url=settings.supabase_url,
            secret_key=settings.supabase_secret_key,
        ) as reader,
        PubSubPublisher(
            project_id=settings.pubsub_project_id,
            emulator_host=settings.pubsub_emulator_host,
            timeout_seconds=settings.pubsub_publish_timeout_seconds,
        ) as publisher,
    ):
        feed = PollingWatchlistFeed(
            reader=reader,
            poll_interval_seconds=settings.watchlist_poll_interval_seconds,
        )
        sink = build_publish_sink(publisher, topic=settings.pubsub_topic_raw_market_data)
        session = FeederSession(
            kabu=kabu,
            watchlist_feed=feed.subscribe(),
            default_exchange=settings.kabu_default_exchange,
            sink=sink,
            backoff=backoff,
            max_pending_sends=settings.sink_max_pending_records,
        )
        return await session.run(iterations=iterations)


@dataclass(frozen=True, slots=True)
class ReplayStats:
    """``run_replay`` の集計結果。"""

    lines_read: int
    payloads_parsed: int
    records_emitted: int
    parse_errors: int


async def run_replay(
    lines: Iterable[str],
    sink: MessageSink,
) -> ReplayStats:
    """録画 JSONL の各行を ``parse_push_message`` に通して sink に流す。

    1 行 = 1 PUSH JSON。空行とコメント行 (``# ...``) はスキップする。
    呼び出し元は ``open(path)`` の戻り値や ``list[str]`` をそのまま渡せる。
    """
    lines_read = 0
    parsed = 0
    emitted = 0
    parse_errors = 0

    for raw in lines:
        lines_read += 1
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        try:
            payload = json.loads(s)
        except json.JSONDecodeError:
            logger.exception("replay: JSON parse failed at line %d", lines_read)
            parse_errors += 1
            continue
        if not isinstance(payload, dict):
            logger.warning(
                "replay: skipping non-object payload at line %d type=%s",
                lines_read,
                type(payload).__name__,
            )
            parse_errors += 1
            continue
        parsed += 1
        for record in parse_push_message(payload):
            await sink(record)
            emitted += 1

    return ReplayStats(
        lines_read=lines_read,
        payloads_parsed=parsed,
        records_emitted=emitted,
        parse_errors=parse_errors,
    )


__all__ = [
    "PubSubPublishSink",
    "ReplayStats",
    "build_publish_sink",
    "run_replay",
    "run_stream",
]
