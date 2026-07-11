from __future__ import annotations

import argparse
import asyncio
import fcntl
import ipaddress
import json
import logging
import os
import sys
import tempfile
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from trade_contracts.logging import configure_logging

from .backtest import iter_features, run_backtest, write_jsonl
from .clients.pubsub import PubSubPublisher, PubSubSubscriber
from .clients.supabase import SupabaseWriter
from .config import StrategyRuleSettings
from .engine import StrategyEngine
from .event_paper.artifact import EventArtifactError, load_event_paper_artifact
from .event_paper.models import (
    EVENT_PUBLISH_ENABLED_ENV,
    EventPaperPublishConfig,
    receipt_dict,
)
from .event_paper.runner import EventPaperPublishError, EventPaperPublisherRunner
from .event_paper.supabase import EventPaperSupabaseClient
from .registry import build_strategies, registered_names
from .strategies import register_builtin
from .streaming.runner import StreamRunner

logger = logging.getLogger(__name__)

_EVENT_PAPER_PUBLISH_LOCK_PATH = Path("/tmp/roboinvest-event-paper-publish.lock")
_EVENT_PAPER_AMBIGUOUS_EXIT_CODE = 3
_EVENT_PAPER_LOCAL_PROJECT_IDS = frozenset({"local-dev", "trade-ai-dev"})


class EventPaperInvocationLockError(RuntimeError):
    """Raised when another event-paper publisher owns the host lock."""


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="strategy-rule",
        description="Strategy Engine A (rule-based) CLI.",
    )
    subparsers = p.add_subparsers(dest="command", required=True)

    bt = subparsers.add_parser(
        "backtest",
        help="ProcessedFeatures JSONL を読んで StrategySignal JSONL を書き出す",
    )
    bt.add_argument(
        "--input",
        dest="input_path",
        type=Path,
        required=True,
        help="ProcessedFeatures JSONL の入力パス。",
    )
    bt.add_argument(
        "--output",
        dest="output_path",
        type=Path,
        default=None,
        help=("StrategySignal JSONL の出力パス。省略時は backtest_output_dir/signals.jsonl。"),
    )

    st = subparsers.add_parser(
        "stream",
        help="processed-features を購読して strategy-signals-a に publish する常駐ループ",
    )
    st.add_argument(
        "--iterations",
        dest="iterations",
        type=int,
        default=None,
        help="(dev) N バッチだけ処理して終了する。未指定で無限ループ。",
    )

    event = subparsers.add_parser(
        "event-paper-publish",
        help="因果検証済み event artifact を fresh book で paper-only publish する",
    )
    event.add_argument("--candidates-json", type=Path, required=True)
    event.add_argument("--output-json", type=Path, required=True)
    event.add_argument("--target-date", type=date.fromisoformat, required=True)
    event.add_argument(
        "--execution-candidate-id",
        help="multi-candidate artifact からこの occurrence だけを選ぶ (1 invocation 1件)。",
    )
    event.add_argument(
        "--publish-paper",
        action="store_true",
        help=f"実発行を許可する明示ラッチ。{EVENT_PUBLISH_ENABLED_ENV}=true も必要。",
    )
    event.add_argument(
        "--max-pull-batches",
        type=int,
        default=300,
        help="fresh candidate books を待つ pull batch 上限。",
    )
    event.add_argument(
        "--no-seek",
        action="store_true",
        help="Pub/Sub emulator のテスト専用。managed Pub/Sub では拒否する。",
    )
    return p


def _run_backtest_cmd(*, input_path: Path, output_path: Path | None) -> int:
    settings = StrategyRuleSettings()
    configure_logging(service="strategy-rule", level=settings.log_level)

    register_builtin()
    strategies = build_strategies(settings)
    if not strategies:
        logger.error(
            "no strategies enabled: strategies_enabled=%s registered=%s",
            settings.strategies_enabled,
            registered_names(),
        )
        return 2

    if not input_path.exists():
        logger.error("input not found: %s", input_path)
        return 2

    engine = StrategyEngine(strategies)
    summary = run_backtest(engine, iter_features(input_path))

    out_path = output_path or (settings.backtest_output_dir / "signals.jsonl")
    write_jsonl(summary.signals, out_path)
    logger.info(
        "backtest done: input=%s output=%s features=%d signals=%d",
        input_path,
        out_path,
        summary.feature_count,
        summary.signal_count,
    )
    return 0


async def _run_stream_cmd(*, iterations: int | None) -> int:
    settings = StrategyRuleSettings()
    configure_logging(service="strategy-rule", level=settings.log_level)

    register_builtin()
    strategies = build_strategies(settings)
    if not strategies:
        logger.warning(
            "no strategies enabled; running stream in no-op mode: "
            "strategies_enabled=%s registered=%s",
            settings.strategies_enabled,
            registered_names(),
        )

    if not settings.supabase_url or not settings.supabase_secret_key:
        logger.error("SUPABASE_URL / SUPABASE_SECRET_KEY が未設定です")
        return 2
    if not settings.pubsub_project_id:
        logger.error("PUBSUB_PROJECT_ID が未設定です")
        return 2

    async with (
        PubSubSubscriber(
            project_id=settings.pubsub_project_id,
            emulator_host=settings.pubsub_emulator_host,
        ) as subscriber,
        PubSubPublisher(
            project_id=settings.pubsub_project_id,
            emulator_host=settings.pubsub_emulator_host,
        ) as publisher,
        SupabaseWriter(
            url=settings.supabase_url,
            secret_key=settings.supabase_secret_key,
        ) as writer,
    ):
        engine = StrategyEngine(strategies)
        runner = StreamRunner(
            subscriber=subscriber,
            publisher=publisher,
            writer=writer,
            engine=engine,
            settings=settings,
        )
        await runner.run(iterations=iterations)

    logger.info("stream done: iterations=%s", iterations)
    return 0


def _env_flag_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _is_loopback_http_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or host is None:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_loopback_host_port(value: str) -> bool:
    try:
        parsed = urlsplit(f"//{value}")
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return False
    if host is None or port is None or parsed.username is not None or parsed.password is not None:
        return False
    if parsed.path or parsed.query or parsed.fragment:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _event_paper_receipt_exit_code(publication_statuses: Iterable[str]) -> int:
    return (
        _EVENT_PAPER_AMBIGUOUS_EXIT_CODE
        if any(status == "ambiguous" for status in publication_statuses)
        else 0
    )


def _verify_receipt_destination(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(path):
        raise FileExistsError(f"receipt destination already exists: {path}")
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.") as probe:
        probe.flush()


def _fsync_directory(path: Path) -> None:
    directory_fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _write_receipt_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
            _fsync_directory(path.parent)


@contextmanager
def _event_paper_publish_lock() -> Iterator[None]:
    flags = os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        lock_fd = os.open(_EVENT_PAPER_PUBLISH_LOCK_PATH, flags, 0o600)
    except OSError as exc:
        raise EventPaperInvocationLockError(
            f"cannot open event-paper publisher lock {_EVENT_PAPER_PUBLISH_LOCK_PATH}: {exc}"
        ) from exc

    try:
        try:
            os.fchmod(lock_fd, 0o600)
        except OSError as exc:
            raise EventPaperInvocationLockError(
                f"cannot secure event-paper publisher lock {_EVENT_PAPER_PUBLISH_LOCK_PATH}: {exc}"
            ) from exc
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise EventPaperInvocationLockError(
                "another event-paper-publish invocation is already running on this host"
            ) from exc
        except OSError as exc:
            raise EventPaperInvocationLockError(
                f"cannot acquire event-paper publisher lock {_EVENT_PAPER_PUBLISH_LOCK_PATH}: {exc}"
            ) from exc
        yield
    finally:
        os.close(lock_fd)


async def _run_event_paper_publish_cmd(
    *,
    candidates_json: Path,
    output_json: Path,
    target_date: date,
    publish_paper: bool,
    max_pull_batches: int,
    no_seek: bool,
    execution_candidate_id: str | None = None,
) -> int:
    settings = StrategyRuleSettings()
    configure_logging(service="strategy-rule-event-paper", level=settings.log_level)
    if not publish_paper:
        logger.error("--publish-paper is required; no signals published")
        return 2
    if not _env_flag_enabled(EVENT_PUBLISH_ENABLED_ENV):
        logger.error("%s=true is required; no signals published", EVENT_PUBLISH_ENABLED_ENV)
        return 2
    if not settings.supabase_url or not settings.supabase_secret_key:
        logger.error("SUPABASE_URL / SUPABASE_SECRET_KEY が未設定です")
        return 2
    if not settings.pubsub_project_id:
        logger.error("PUBSUB_PROJECT_ID が未設定です")
        return 2
    if no_seek and not settings.pubsub_emulator_host:
        logger.error("--no-seek is allowed only with PUBSUB_EMULATOR_HOST")
        return 2
    if settings.pubsub_emulator_host and not no_seek:
        logger.error(
            "PUBSUB_EMULATOR_HOST is allowed only for an explicit --no-seek test run; "
            "managed publication refused"
        )
        return 2
    if not settings.pubsub_emulator_host:
        logger.error(
            "managed event-paper publication is disabled until the frozen next-open / "
            "20th-session-close execution contract is implemented"
        )
        return 2
    if not _is_loopback_host_port(settings.pubsub_emulator_host):
        logger.error(
            "event-paper transport stress requires a loopback PUBSUB_EMULATOR_HOST; "
            "remote emulator refused"
        )
        return 2
    if not _is_loopback_http_url(settings.supabase_url):
        logger.error(
            "event-paper transport stress requires a loopback SUPABASE_URL; "
            "non-local Supabase refused"
        )
        return 2
    if settings.pubsub_project_id not in _EVENT_PAPER_LOCAL_PROJECT_IDS:
        logger.error(
            "event-paper transport stress requires a non-production PUBSUB_PROJECT_ID; allowed=%s",
            ",".join(sorted(_EVENT_PAPER_LOCAL_PROJECT_IDS)),
        )
        return 2
    if candidates_json.resolve() == output_json.resolve():
        logger.error("--output-json must differ from --candidates-json")
        return 2
    try:
        with _event_paper_publish_lock():
            try:
                _verify_receipt_destination(output_json)
                artifact = load_event_paper_artifact(candidates_json)
                artifact.artifact.validate_target_date(target_date)
                config = EventPaperPublishConfig(
                    max_pull_batches=max_pull_batches,
                    seek_before_pull=not no_seek,
                )
            except (EventArtifactError, OSError, ValueError) as exc:
                logger.error("event paper artifact/config rejected: %s", exc)
                return 2

            try:
                async with (
                    PubSubSubscriber(
                        project_id=settings.pubsub_project_id,
                        emulator_host=settings.pubsub_emulator_host,
                    ) as subscriber,
                    PubSubPublisher(
                        project_id=settings.pubsub_project_id,
                        emulator_host=settings.pubsub_emulator_host,
                    ) as publisher,
                    EventPaperSupabaseClient(
                        url=settings.supabase_url,
                        secret_key=settings.supabase_secret_key,
                    ) as supabase,
                ):
                    receipt = await EventPaperPublisherRunner(
                        artifact=artifact,
                        target_date=target_date,
                        subscriber=subscriber,
                        publisher=publisher,
                        supabase=supabase,
                        execution_candidate_id=execution_candidate_id,
                        config=config,
                    ).run()
            except EventPaperPublishError as exc:
                logger.error("event paper publish failed closed: %s", exc)
                return 1

            try:
                _write_receipt_atomic(output_json, receipt_dict(receipt))
            except OSError as exc:
                logger.error("event paper receipt write failed after publish: %s", exc)
                return 1
            receipt_exit_code = _event_paper_receipt_exit_code(
                record.publication_status for record in receipt.published
            )
            if receipt_exit_code:
                logger.error(
                    "event paper publication remains ambiguous; receipt persisted for "
                    "investigation without republish: output=%s candidates=%s",
                    output_json,
                    ",".join(receipt.selected_execution_candidate_ids),
                )
                return receipt_exit_code
            logger.info(
                "event paper publish confirmed: candidates=%d output=%s",
                len(receipt.published),
                output_json,
            )
            return 0
    except EventPaperInvocationLockError as exc:
        logger.error("event paper publish refused before network I/O: %s", exc)
        return 2


def main() -> None:
    args = _build_parser().parse_args()
    if args.command == "backtest":
        sys.exit(_run_backtest_cmd(input_path=args.input_path, output_path=args.output_path))
    if args.command == "stream":
        sys.exit(asyncio.run(_run_stream_cmd(iterations=args.iterations)))
    if args.command == "event-paper-publish":
        sys.exit(
            asyncio.run(
                _run_event_paper_publish_cmd(
                    candidates_json=args.candidates_json,
                    output_json=args.output_json,
                    target_date=args.target_date,
                    publish_paper=args.publish_paper,
                    max_pull_batches=args.max_pull_batches,
                    no_seek=args.no_seek,
                    execution_candidate_id=args.execution_candidate_id,
                )
            )
        )
    raise SystemExit(f"unknown command: {args.command}")


if __name__ == "__main__":
    main()
