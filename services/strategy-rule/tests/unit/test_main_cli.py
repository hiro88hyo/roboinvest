from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import strategy_rule.__main__ as main_module
from strategy_rule.__main__ import (
    EventPaperInvocationLockError,
    _build_parser,
    _event_paper_publish_lock,
    _event_paper_receipt_exit_code,
    _run_backtest_cmd,
    _run_event_paper_publish_cmd,
    _run_stream_cmd,
    _verify_receipt_destination,
    _write_receipt_atomic,
)
from strategy_rule.event_paper._testing import make_event_artifact_payload
from strategy_rule.event_paper.artifact import load_event_paper_artifact
from strategy_rule.event_paper.models import (
    EVENT_EXECUTION_STRATEGY_KEY,
    EventPaperPublishedRecord,
    EventPaperPublishReceipt,
)
from trade_contracts.features import ProcessedFeatures
from trade_contracts.signal import StrategySignal


def _write_features(path: Path, features: list[ProcessedFeatures]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(f.model_dump_json() for f in features) + "\n", encoding="utf-8")


def test_parser_requires_subcommand() -> None:
    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_parser_requires_input() -> None:
    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["backtest"])


def test_parser_parses_paths(tmp_path: Path) -> None:
    parser = _build_parser()
    args = parser.parse_args(
        ["backtest", "--input", str(tmp_path / "in.jsonl"), "--output", str(tmp_path / "out.jsonl")]
    )
    assert args.command == "backtest"
    assert args.input_path == tmp_path / "in.jsonl"
    assert args.output_path == tmp_path / "out.jsonl"


def test_run_backtest_writes_signals_for_sma_cross(
    tmp_path: Path,
    features_factory: Callable[..., ProcessedFeatures],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = datetime(2026, 4, 20, 9, 0, tzinfo=UTC)
    feats = [
        features_factory(
            timestamp=base,
            price=Decimal("1000"),
            sma_short=Decimal("99"),
            sma_long=Decimal("100"),
        ),
        features_factory(
            timestamp=base + timedelta(minutes=1),
            price=Decimal("1000"),
            sma_short=Decimal("120"),
            sma_long=Decimal("100"),
        ),
    ]
    in_path = tmp_path / "features.jsonl"
    out_path = tmp_path / "signals.jsonl"
    _write_features(in_path, feats)
    monkeypatch.setenv("STRATEGIES_ENABLED", "sma_crossover")

    rc = _run_backtest_cmd(input_path=in_path, output_path=out_path)
    assert rc == 0
    lines = out_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 1
    signal = StrategySignal.model_validate_json(lines[0])
    assert signal.symbol == "7203"


def test_run_backtest_returns_2_when_input_missing(tmp_path: Path) -> None:
    rc = _run_backtest_cmd(input_path=tmp_path / "nope.jsonl", output_path=tmp_path / "out.jsonl")
    assert rc == 2


def test_run_backtest_returns_2_when_no_strategies_enabled(
    tmp_path: Path,
    features_factory: Callable[..., ProcessedFeatures],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    in_path = tmp_path / "features.jsonl"
    out_path = tmp_path / "signals.jsonl"
    _write_features(in_path, [features_factory()])
    monkeypatch.setenv("STRATEGIES_ENABLED", "")

    rc = _run_backtest_cmd(input_path=in_path, output_path=out_path)
    assert rc == 2
    assert not out_path.exists()


def test_parser_parses_stream_iterations() -> None:
    parser = _build_parser()
    args = parser.parse_args(["stream", "--iterations", "3"])
    assert args.command == "stream"
    assert args.iterations == 3


async def test_run_stream_cmd_returns_2_when_supabase_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STRATEGIES_ENABLED", "rsi_threshold")
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "")
    monkeypatch.setenv("PUBSUB_PROJECT_ID", "trade-ai-dev")
    rc = await _run_stream_cmd(iterations=1)
    assert rc == 2


async def test_run_stream_cmd_returns_2_when_pubsub_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STRATEGIES_ENABLED", "rsi_threshold")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "k")
    monkeypatch.setenv("PUBSUB_PROJECT_ID", "")
    rc = await _run_stream_cmd(iterations=1)
    assert rc == 2


async def test_run_stream_cmd_allows_no_strategies_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STRATEGIES_ENABLED", "")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "k")
    monkeypatch.setenv("PUBSUB_PROJECT_ID", "trade-ai-dev")
    monkeypatch.setenv("PUBSUB_EMULATOR_HOST", "localhost:8085")
    rc = await _run_stream_cmd(iterations=0)
    assert rc == 0


def test_parser_requires_explicit_event_publish_latch(tmp_path: Path) -> None:
    args = _build_parser().parse_args(
        [
            "event-paper-publish",
            "--candidates-json",
            str(tmp_path / "candidates.json"),
            "--output-json",
            str(tmp_path / "receipt.json"),
            "--target-date",
            "2026-01-21",
        ]
    )
    assert args.command == "event-paper-publish"
    assert args.publish_paper is False


def test_ambiguous_receipt_status_is_not_a_success_exit() -> None:
    assert _event_paper_receipt_exit_code(["confirmed"]) == 0
    assert _event_paper_receipt_exit_code(["ambiguous"]) == 3
    assert _event_paper_receipt_exit_code(["confirmed", "ambiguous"]) == 3


async def test_event_publish_cmd_persists_ambiguous_receipt_and_exits_nonzero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = tmp_path / "candidates.json"
    candidates.write_text(json.dumps(make_event_artifact_payload()), encoding="utf-8")
    loaded = load_event_paper_artifact(candidates)
    output = tmp_path / "receipt.json"
    attempted_at = datetime(2026, 1, 21, 0, 1, tzinfo=UTC)
    receipt = EventPaperPublishReceipt(
        target_date=date(2026, 1, 21),
        artifact_path=str(candidates),
        artifact_sha256=loaded.sha256,
        selected_execution_candidate_ids=["cluster-7203:obs-7203"],
        published=[
            EventPaperPublishedRecord(
                strategy_key=EVENT_EXECUTION_STRATEGY_KEY,
                execution_candidate_id="cluster-7203:obs-7203",
                symbol="7203",
                signal_id="00000000-0000-0000-0000-000000000001",
                raw_book_message_id="raw-book-1",
                observed_ask="1000",
                book_received_at=attempted_at,
                publication_status="ambiguous",
                publication_attempt_id="attempt-1",
                attempted_at=attempted_at,
                strategy_message_id=None,
                topic="strategy-signals-a",
                published_at=None,
                artifact_sha256=loaded.sha256,
            )
        ],
        skipped_messages={},
    )

    class _FakeClient:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            pass

    class _FakeRunner:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def run(self) -> EventPaperPublishReceipt:
            return receipt

    monkeypatch.setenv("EVENT_CLUSTER_PAPER_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("SUPABASE_URL", "http://127.0.0.1:54321")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "k")
    monkeypatch.setenv("PUBSUB_PROJECT_ID", "trade-ai-dev")
    monkeypatch.setenv("PUBSUB_EMULATOR_HOST", "localhost:8085")
    monkeypatch.setattr(
        main_module,
        "_EVENT_PAPER_PUBLISH_LOCK_PATH",
        tmp_path / "event-paper.lock",
    )
    monkeypatch.setattr(main_module, "PubSubSubscriber", _FakeClient)
    monkeypatch.setattr(main_module, "PubSubPublisher", _FakeClient)
    monkeypatch.setattr(main_module, "EventPaperSupabaseClient", _FakeClient)
    monkeypatch.setattr(main_module, "EventPaperPublisherRunner", _FakeRunner)

    rc = await _run_event_paper_publish_cmd(
        candidates_json=candidates,
        output_json=output,
        target_date=date(2026, 1, 21),
        publish_paper=True,
        max_pull_batches=1,
        no_seek=True,
    )

    assert rc == 3
    assert (
        json.loads(output.read_text(encoding="utf-8"))["published"][0]["publication_status"]
        == "ambiguous"
    )


async def test_event_publish_cmd_requires_both_latches_before_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = tmp_path / "candidates.json"
    candidates.write_text(json.dumps(make_event_artifact_payload()), encoding="utf-8")
    output = tmp_path / "receipt.json"
    monkeypatch.delenv("EVENT_CLUSTER_PAPER_PUBLISH_ENABLED", raising=False)

    without_cli = await _run_event_paper_publish_cmd(
        candidates_json=candidates,
        output_json=output,
        target_date=date(2026, 1, 21),
        publish_paper=False,
        max_pull_batches=1,
        no_seek=False,
    )
    with_cli_only = await _run_event_paper_publish_cmd(
        candidates_json=candidates,
        output_json=output,
        target_date=date(2026, 1, 21),
        publish_paper=True,
        max_pull_batches=1,
        no_seek=False,
    )

    assert without_cli == 2
    assert with_cli_only == 2
    assert not output.exists()


async def test_event_publish_cmd_rejects_no_seek_on_managed_pubsub(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = tmp_path / "candidates.json"
    candidates.write_text(json.dumps(make_event_artifact_payload()), encoding="utf-8")
    monkeypatch.setenv("EVENT_CLUSTER_PAPER_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "k")
    monkeypatch.setenv("PUBSUB_PROJECT_ID", "trade-ai-prod")
    monkeypatch.delenv("PUBSUB_EMULATOR_HOST", raising=False)

    rc = await _run_event_paper_publish_cmd(
        candidates_json=candidates,
        output_json=tmp_path / "receipt.json",
        target_date=date(2026, 1, 21),
        publish_paper=True,
        max_pull_batches=1,
        no_seek=True,
    )

    assert rc == 2


async def test_event_publish_cmd_rejects_managed_stress_profile_before_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = tmp_path / "candidates.json"
    candidates.write_text(json.dumps(make_event_artifact_payload()), encoding="utf-8")
    monkeypatch.setenv("EVENT_CLUSTER_PAPER_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "k")
    monkeypatch.setenv("PUBSUB_PROJECT_ID", "trade-ai-prod")
    monkeypatch.delenv("PUBSUB_EMULATOR_HOST", raising=False)

    def fail_network(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Pub/Sub client constructed for a blocked managed stress run")

    monkeypatch.setattr(main_module, "PubSubSubscriber", fail_network)

    rc = await _run_event_paper_publish_cmd(
        candidates_json=candidates,
        output_json=tmp_path / "receipt.json",
        target_date=date(2026, 1, 21),
        publish_paper=True,
        max_pull_batches=1,
        no_seek=False,
    )

    assert rc == 2


async def test_event_publish_cmd_rejects_implicit_emulator_before_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = tmp_path / "candidates.json"
    candidates.write_text(json.dumps(make_event_artifact_payload()), encoding="utf-8")
    monkeypatch.setenv("EVENT_CLUSTER_PAPER_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "k")
    monkeypatch.setenv("PUBSUB_PROJECT_ID", "trade-ai-dev")
    monkeypatch.setenv("PUBSUB_EMULATOR_HOST", "localhost:8085")

    def fail_network(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Pub/Sub client constructed for an implicit emulator run")

    monkeypatch.setattr(main_module, "PubSubSubscriber", fail_network)

    rc = await _run_event_paper_publish_cmd(
        candidates_json=candidates,
        output_json=tmp_path / "receipt.json",
        target_date=date(2026, 1, 21),
        publish_paper=True,
        max_pull_batches=1,
        no_seek=False,
    )

    assert rc == 2


@pytest.mark.parametrize(
    ("supabase_url", "project_id"),
    [
        ("https://example.supabase.co", "trade-ai-dev"),
        ("http://127.0.0.1:54321", "trade-ai-prod"),
    ],
)
async def test_event_publish_cmd_rejects_nonlocal_stress_environment_before_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    supabase_url: str,
    project_id: str,
) -> None:
    candidates = tmp_path / "candidates.json"
    candidates.write_text(json.dumps(make_event_artifact_payload()), encoding="utf-8")
    monkeypatch.setenv("EVENT_CLUSTER_PAPER_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("SUPABASE_URL", supabase_url)
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "k")
    monkeypatch.setenv("PUBSUB_PROJECT_ID", project_id)
    monkeypatch.setenv("PUBSUB_EMULATOR_HOST", "localhost:8085")

    def fail_network(*_args: object, **_kwargs: object) -> None:
        pytest.fail("client constructed for a non-local transport stress run")

    monkeypatch.setattr(main_module, "PubSubSubscriber", fail_network)
    monkeypatch.setattr(main_module, "EventPaperSupabaseClient", fail_network)

    rc = await _run_event_paper_publish_cmd(
        candidates_json=candidates,
        output_json=tmp_path / "receipt.json",
        target_date=date(2026, 1, 21),
        publish_paper=True,
        max_pull_batches=1,
        no_seek=True,
    )

    assert rc == 2


async def test_event_publish_cmd_rejects_remote_emulator_before_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = tmp_path / "candidates.json"
    candidates.write_text(json.dumps(make_event_artifact_payload()), encoding="utf-8")
    monkeypatch.setenv("EVENT_CLUSTER_PAPER_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("SUPABASE_URL", "http://127.0.0.1:54321")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "k")
    monkeypatch.setenv("PUBSUB_PROJECT_ID", "trade-ai-dev")
    monkeypatch.setenv("PUBSUB_EMULATOR_HOST", "pubsub.example.com:8085")

    def fail_network(*_args: object, **_kwargs: object) -> None:
        pytest.fail("client constructed for a remote Pub/Sub emulator")

    monkeypatch.setattr(main_module, "PubSubSubscriber", fail_network)
    monkeypatch.setattr(main_module, "EventPaperSupabaseClient", fail_network)

    rc = await _run_event_paper_publish_cmd(
        candidates_json=candidates,
        output_json=tmp_path / "receipt.json",
        target_date=date(2026, 1, 21),
        publish_paper=True,
        max_pull_batches=1,
        no_seek=True,
    )

    assert rc == 2


def test_verify_receipt_destination_rejects_existing_file(tmp_path: Path) -> None:
    output = tmp_path / "receipt.json"
    output.write_text("original\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="already exists"):
        _verify_receipt_destination(output)

    assert output.read_text(encoding="utf-8") == "original\n"


def test_write_receipt_atomic_is_durable_and_does_not_clobber(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "receipts" / "receipt.json"
    fsynced_directories: list[Path] = []
    monkeypatch.setattr(
        main_module,
        "_fsync_directory",
        lambda path: fsynced_directories.append(path),
    )

    _write_receipt_atomic(output, {"published": ["signal-1"]})

    assert json.loads(output.read_text(encoding="utf-8")) == {"published": ["signal-1"]}
    assert output.read_text(encoding="utf-8").endswith("\n")
    assert fsynced_directories == [output.parent]
    assert not list(output.parent.glob(f".{output.name}.*.tmp"))

    with pytest.raises(FileExistsError):
        _write_receipt_atomic(output, {"published": ["signal-2"]})

    assert json.loads(output.read_text(encoding="utf-8")) == {"published": ["signal-1"]}
    assert not list(output.parent.glob(f".{output.name}.*.tmp"))


def test_write_receipt_atomic_cleans_temp_after_link_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "receipt.json"

    def fail_link(_source: Path, _destination: Path) -> None:
        raise OSError("link failed")

    monkeypatch.setattr(os, "link", fail_link)

    with pytest.raises(OSError, match="link failed"):
        _write_receipt_atomic(output, {"published": []})

    assert not output.exists()
    assert not list(tmp_path.glob(f".{output.name}.*.tmp"))


def test_event_paper_publish_lock_is_nonblocking_and_reusable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        main_module,
        "_EVENT_PAPER_PUBLISH_LOCK_PATH",
        tmp_path / "event-paper.lock",
    )

    with (
        _event_paper_publish_lock(),
        pytest.raises(EventPaperInvocationLockError, match="already running"),
        _event_paper_publish_lock(),
    ):
        pytest.fail("concurrent lock acquisition unexpectedly succeeded")

    with _event_paper_publish_lock():
        pass


async def test_event_publish_cmd_rejects_existing_receipt_before_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = tmp_path / "candidates.json"
    candidates.write_text(json.dumps(make_event_artifact_payload()), encoding="utf-8")
    output = tmp_path / "receipt.json"
    output.write_text("do not replace\n", encoding="utf-8")
    monkeypatch.setenv("EVENT_CLUSTER_PAPER_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("SUPABASE_URL", "http://127.0.0.1:54321")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "k")
    monkeypatch.setenv("PUBSUB_PROJECT_ID", "trade-ai-dev")
    monkeypatch.setenv("PUBSUB_EMULATOR_HOST", "localhost:8085")
    monkeypatch.setattr(
        main_module,
        "_EVENT_PAPER_PUBLISH_LOCK_PATH",
        tmp_path / "event-paper.lock",
    )

    def fail_network(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Pub/Sub client constructed before receipt destination rejection")

    monkeypatch.setattr(main_module, "PubSubSubscriber", fail_network)

    rc = await _run_event_paper_publish_cmd(
        candidates_json=candidates,
        output_json=output,
        target_date=date(2026, 1, 21),
        publish_paper=True,
        max_pull_batches=1,
        no_seek=True,
    )

    assert rc == 2
    assert output.read_text(encoding="utf-8") == "do not replace\n"


async def test_event_publish_cmd_rejects_concurrent_invocation_before_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = tmp_path / "candidates.json"
    candidates.write_text(json.dumps(make_event_artifact_payload()), encoding="utf-8")
    output = tmp_path / "receipt.json"
    monkeypatch.setenv("EVENT_CLUSTER_PAPER_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("SUPABASE_URL", "http://127.0.0.1:54321")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "k")
    monkeypatch.setenv("PUBSUB_PROJECT_ID", "trade-ai-dev")
    monkeypatch.setenv("PUBSUB_EMULATOR_HOST", "localhost:8085")
    monkeypatch.setattr(
        main_module,
        "_EVENT_PAPER_PUBLISH_LOCK_PATH",
        tmp_path / "event-paper.lock",
    )

    def fail_network(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Pub/Sub client constructed while invocation lock was held")

    monkeypatch.setattr(main_module, "PubSubSubscriber", fail_network)

    with _event_paper_publish_lock():
        rc = await _run_event_paper_publish_cmd(
            candidates_json=candidates,
            output_json=output,
            target_date=date(2026, 1, 21),
            publish_paper=True,
            max_pull_batches=1,
            no_seek=True,
        )

    assert rc == 2
    assert not output.exists()
