from __future__ import annotations

import copy
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
from strategy_rule.event_paper._testing import (
    make_event_artifact_payload,
    make_event_book,
    make_event_candidate,
)
from strategy_rule.event_paper.artifact import (
    LoadedEventPaperArtifact,
    load_event_paper_artifact,
)
from strategy_rule.event_paper.models import (
    EVENT_EXECUTION_STRATEGY_KEY,
    EventPaperPublicationAttempt,
    EventPaperPublicationCheckpoint,
    EventPaperPublishConfig,
    EventPaperPublishReceipt,
    EventPaperSignalClaim,
    claim_json,
)
from strategy_rule.event_paper.publisher import build_signal_claim
from trade_contracts.enums import Action, SignalSource
from trade_contracts.signal import deterministic_strategy_signal_id


def _load_module() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "report-event-paper-observation.py"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("report_event_paper_observation", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


report_event_paper_observation = _load_module()


def test_to_date_filter_is_inclusive_in_jst() -> None:
    filters: dict[str, str] = {}

    report_event_paper_observation._apply_executed_at_bounds(
        filters,
        from_date=date(2026, 1, 21),
        to_date=date(2026, 1, 21),
    )

    assert filters["and"] == (
        "(executed_at.gte.2026-01-20T15:00:00+00:00,executed_at.lt.2026-01-21T15:00:00+00:00)"
    )


def _write_artifact(
    tmp_path: Path,
    *,
    candidates: list[dict[str, Any]] | None = None,
) -> LoadedEventPaperArtifact:
    payload = make_event_artifact_payload(
        **({} if candidates is None else {"candidates": candidates})
    )
    path = tmp_path / "candidates.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return load_event_paper_artifact(path)


def _published_record(
    *,
    artifact: LoadedEventPaperArtifact,
    candidate_index: int = 0,
    observed_ask: str = "1000",
    publication_status: str = "confirmed",
) -> dict[str, Any]:
    candidate = artifact.artifact.candidates[candidate_index]
    signal_id = deterministic_strategy_signal_id(
        strategy_key=EVENT_EXECUTION_STRATEGY_KEY,
        candidate_id=candidate.execution_candidate_id,
        source=SignalSource.RULE,
        symbol=candidate.symbol,
        action=Action.BUY,
    )
    return {
        "strategy_key": EVENT_EXECUTION_STRATEGY_KEY,
        "execution_candidate_id": candidate.execution_candidate_id,
        "symbol": candidate.symbol,
        "signal_id": str(signal_id),
        "raw_book_message_id": f"raw-{candidate.symbol}",
        "observed_ask": observed_ask,
        "book_received_at": "2026-01-21T00:01:00+00:00",
        "publication_status": publication_status,
        "publication_attempt_id": f"attempt-{candidate.symbol}",
        "attempted_at": "2026-01-21T00:01:00.500000+00:00",
        "strategy_message_id": (
            f"strategy-{candidate.symbol}" if publication_status == "confirmed" else None
        ),
        "topic": "strategy-signals-a",
        "published_at": (
            "2026-01-21T00:01:01+00:00" if publication_status == "confirmed" else None
        ),
        "artifact_sha256": artifact.sha256,
    }


def _receipt_payload(
    artifact: LoadedEventPaperArtifact,
    *,
    published: list[dict[str, Any]] | None = None,
    selected_execution_candidate_ids: list[str] | None = None,
) -> dict[str, Any]:
    records = (
        [
            _published_record(artifact=artifact, candidate_index=index)
            for index in range(len(artifact.artifact.candidates))
        ]
        if published is None
        else published
    )
    return {
        "schema_version": 1,
        "mode": "paper_publish",
        "target_date": "2026-01-21",
        "artifact_path": str(artifact.source_path),
        "artifact_sha256": artifact.sha256,
        "selected_execution_candidate_ids": (
            [candidate.execution_candidate_id for candidate in artifact.artifact.candidates]
            if selected_execution_candidate_ids is None
            else selected_execution_candidate_ids
        ),
        "published": records,
        "skipped_messages": {},
    }


def _receipt(artifact: LoadedEventPaperArtifact) -> EventPaperPublishReceipt:
    receipt = EventPaperPublishReceipt.model_validate(_receipt_payload(artifact))
    report_event_paper_observation.validate_publish_receipt(
        artifact=artifact,
        receipt=receipt,
    )
    return receipt


def _strategy_log_row(
    artifact: LoadedEventPaperArtifact,
    receipt: EventPaperPublishReceipt,
) -> dict[str, Any]:
    published = receipt.published[0]
    candidate = next(
        row
        for row in artifact.artifact.candidates
        if row.execution_candidate_id == published.execution_candidate_id
    )
    claim, signal = build_signal_claim(
        candidate=candidate,
        book=make_event_book(
            symbol=candidate.symbol,
            received_at=published.book_received_at,
            best_bid=str(published.observed_ask - 1),
            best_ask=str(published.observed_ask),
        ),
        raw_book_message_id=published.raw_book_message_id,
        artifact_sha256=artifact.sha256,
        config=EventPaperPublishConfig(),
    )
    assert str(signal.signal_id) == published.signal_id
    claim_payload = claim.model_dump(mode="python")
    claim_payload["publication_attempt"] = EventPaperPublicationAttempt(
        attempt_id=published.publication_attempt_id,
        attempted_at=published.attempted_at,
    )
    if published.publication_status == "confirmed":
        assert published.strategy_message_id is not None
        assert published.published_at is not None
        claim_payload["publication"] = EventPaperPublicationCheckpoint(
            attempt_id=published.publication_attempt_id,
            topic=published.topic,
            strategy_message_id=published.strategy_message_id,
            published_at=published.published_at,
        )
    checkpointed_claim = EventPaperSignalClaim.model_validate(claim_payload)
    return {
        "signal_id": published.signal_id,
        "source": "RULE",
        "symbol": candidate.symbol,
        "action": "BUY",
        "confidence": signal.confidence,
        "reasoning": claim_json(checkpointed_claim),
        "created_at": signal.created_at.isoformat(),
    }


def test_build_report_candidate_only_marks_dry_run(tmp_path: Path) -> None:
    artifact = _write_artifact(tmp_path)

    report = report_event_paper_observation.build_report(
        artifact.artifact,
        receipt=None,
        rows=None,
    )

    assert report["source_mode"] == "dry_run"
    assert report["summary"]["with_supabase"] is False
    assert report["summary"]["published_count"] == 0
    assert report["summary"]["ambiguous_count"] == 0
    assert report["summary"]["feature_data_incomplete_count"] == 0
    assert report["summary"]["status_counts"] == {"dry_run_only": 1}
    assert report["rows"][0]["feature_data_complete"] is True
    row = report["rows"][0]
    assert row["execution_candidate_id"] == "cluster-7203:obs-7203"
    assert row["symbol"] == "7203"
    assert row["intended_entry_price"] is None
    assert row["observed_ask"] is None
    assert row["strategy_signal_id"] is None
    assert row["reconciliation_status"] == "dry_run_only"


def test_build_report_accepts_zero_candidate_dry_run(tmp_path: Path) -> None:
    artifact = _write_artifact(tmp_path, candidates=[])

    report = report_event_paper_observation.build_report(
        artifact.artifact,
        receipt=None,
        rows=None,
    )

    assert report["summary"]["candidate_count"] == 0
    assert report["summary"]["status_counts"] == {}
    assert report["rows"] == []


def test_fetch_supabase_rows_accepts_zero_candidate_dry_run(tmp_path: Path) -> None:
    artifact = _write_artifact(tmp_path, candidates=[])
    requests: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500)

    with httpx.Client(
        base_url="https://example.supabase.co",
        transport=httpx.MockTransport(_handler),
    ) as client:
        rows = report_event_paper_observation.fetch_supabase_rows(
            client,
            artifact=artifact.artifact,
            receipt=None,
        )

    assert requests == []
    assert rows == report_event_paper_observation.SupabaseRows([], [], [], [])


def test_receipt_without_supabase_is_published_unqueried(tmp_path: Path) -> None:
    artifact = _write_artifact(tmp_path)
    receipt = _receipt(artifact)

    report = report_event_paper_observation.build_report(
        artifact.artifact,
        receipt=receipt,
        rows=None,
    )

    assert report["source_mode"] == "paper_publish"
    assert report["summary"]["published_count"] == 1
    assert report["summary"]["ambiguous_count"] == 0
    assert report["summary"]["execution_profile"] == "opening_transport_stress_v1"
    assert report["summary"]["comparable_to_registered_backtest"] is False
    assert report["summary"]["status_counts"] == {"published_unqueried": 1}
    row = report["rows"][0]
    assert row["observed_ask"] == "1000"
    assert row["intended_entry_price"] == "1000"
    assert row["book_received_at"] == "2026-01-21T00:01:00+00:00"
    assert row["publication_status"] == "confirmed"
    assert row["publication_attempt_id"] == "attempt-7203"
    assert row["attempted_at"] == "2026-01-21T00:01:00.500000+00:00"
    assert row["source_received_at"] == "2026-01-20T15:30:00+00:00"
    assert row["comparable_to_registered_backtest"] is False
    assert row["strategy_signal_id"] == receipt.published[0].signal_id


def test_ambiguous_receipt_with_matching_attempt_is_reportable(tmp_path: Path) -> None:
    artifact = _write_artifact(tmp_path)
    receipt = EventPaperPublishReceipt.model_validate(
        _receipt_payload(
            artifact,
            published=[_published_record(artifact=artifact, publication_status="ambiguous")],
        )
    )
    report_event_paper_observation.validate_publish_receipt(
        artifact=artifact,
        receipt=receipt,
    )
    rows = report_event_paper_observation.SupabaseRows(
        strategy_logs=[_strategy_log_row(artifact, receipt)],
        aggregator_logs=[],
        trades_paper=[],
        positions=[],
    )

    report = report_event_paper_observation.build_report(
        artifact.artifact,
        receipt=receipt,
        rows=rows,
    )

    assert report["summary"]["published_count"] == 0
    assert report["summary"]["ambiguous_count"] == 1
    assert report["summary"]["status_counts"] == {"publication_ambiguous": 1}
    row = report["rows"][0]
    assert row["strategy_log_found"] is True
    assert row["publication_status"] == "ambiguous"
    assert row["publication_attempt_id"] == "attempt-7203"
    assert row["strategy_message_id"] is None
    assert row["published_at"] is None


def test_receipt_join_uses_execution_candidate_id_not_record_order(tmp_path: Path) -> None:
    second = make_event_candidate(
        execution_candidate_id="cluster-6758:obs-6758",
        cluster_id="cluster-6758",
        observation_id="obs-6758",
        event_id="event-6758-earnings",
        event_ids=["event-6758-earnings", "event-6758-dividend"],
        symbol="6758",
        symbol_name="ソニーグループ",
    )
    artifact = _write_artifact(
        tmp_path,
        candidates=[make_event_candidate(), second],
    )
    records = [
        _published_record(artifact=artifact, candidate_index=1, observed_ask="2000"),
        _published_record(artifact=artifact, candidate_index=0, observed_ask="1000"),
    ]
    receipt = EventPaperPublishReceipt.model_validate(_receipt_payload(artifact, published=records))
    report_event_paper_observation.validate_publish_receipt(
        artifact=artifact,
        receipt=receipt,
    )

    report = report_event_paper_observation.build_report(
        artifact.artifact,
        receipt=receipt,
        rows=None,
    )

    assert [(row["symbol"], row["observed_ask"]) for row in report["rows"]] == [
        ("7203", "1000"),
        ("6758", "2000"),
    ]


def test_receipt_can_cover_one_explicit_occurrence_from_multi_artifact(tmp_path: Path) -> None:
    second = make_event_candidate(
        execution_candidate_id="cluster-6758:obs-6758",
        cluster_id="cluster-6758",
        observation_id="obs-6758",
        symbol="6758",
        symbol_name="ソニーグループ",
    )
    artifact = _write_artifact(tmp_path, candidates=[make_event_candidate(), second])
    record = _published_record(artifact=artifact, candidate_index=1, observed_ask="2000")
    receipt = EventPaperPublishReceipt.model_validate(
        _receipt_payload(
            artifact,
            published=[record],
            selected_execution_candidate_ids=["cluster-6758:obs-6758"],
        )
    )

    report_event_paper_observation.validate_publish_receipt(
        artifact=artifact,
        receipt=receipt,
    )
    report = report_event_paper_observation.build_report(
        artifact.artifact,
        receipt=receipt,
        rows=None,
    )

    assert [row["reconciliation_status"] for row in report["rows"]] == [
        "not_selected_in_receipt",
        "published_unqueried",
    ]


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda payload: payload.update(artifact_sha256="0" * 64), "artifact_sha256"),
        (lambda payload: payload.update(target_date="2026-01-22"), "target_date"),
        (
            lambda payload: payload["published"][0].update(symbol="6758"),
            "symbol mismatch",
        ),
        (
            lambda payload: payload["published"][0].update(topic="live-orders"),
            "topic mismatch",
        ),
        (
            lambda payload: payload["published"][0].update(
                signal_id="00000000-0000-0000-0000-000000000000"
            ),
            "signal_id mismatch",
        ),
        (
            lambda payload: payload["published"][0].update(artifact_sha256="0" * 64),
            "record artifact_sha256",
        ),
        (
            lambda payload: payload["published"][0].update(
                published_at="2026-01-21T00:30:00+00:00"
            ),
            "outside target entry window",
        ),
        (
            lambda payload: payload["published"][0].update(
                published_at="2026-01-21T00:01:11+00:00"
            ),
            "stale selected book",
        ),
        (
            lambda payload: payload["published"][0].update(
                attempted_at="2026-01-21T00:01:11+00:00"
            ),
            "publication attempt used an invalid selected book",
        ),
        (
            lambda payload: payload["published"][0].update(
                book_received_at="2026-01-21T00:01:06+00:00"
            ),
            "publication attempt used an invalid selected book",
        ),
        (
            lambda payload: payload["published"][0].update(
                book_received_at="2026-01-21T00:29:59+00:00",
                attempted_at="2026-01-21T00:30:00+00:00",
            ),
            "publication attempt outside target entry window",
        ),
        (
            lambda payload: payload["published"][0].update(
                attempted_at="2026-01-21T00:01:02+00:00"
            ),
            "publication predates durable attempt",
        ),
    ],
)
def test_validate_receipt_rejects_cross_document_mismatch(
    tmp_path: Path,
    mutation: Any,
    match: str,
) -> None:
    artifact = _write_artifact(tmp_path)
    payload = _receipt_payload(artifact)
    mutation(payload)
    receipt = EventPaperPublishReceipt.model_validate(payload)

    with pytest.raises(report_event_paper_observation.ObservationInputError, match=match):
        report_event_paper_observation.validate_publish_receipt(
            artifact=artifact,
            receipt=receipt,
        )


def test_validate_receipt_accepts_frozen_future_skew_tolerance(tmp_path: Path) -> None:
    artifact = _write_artifact(tmp_path)
    payload = _receipt_payload(artifact)
    payload["published"][0]["book_received_at"] = "2026-01-21T00:01:04+00:00"
    receipt = EventPaperPublishReceipt.model_validate(payload)

    report_event_paper_observation.validate_publish_receipt(
        artifact=artifact,
        receipt=receipt,
    )


def test_receipt_cannot_claim_registered_backtest_comparability(tmp_path: Path) -> None:
    artifact = _write_artifact(tmp_path)
    payload = _receipt_payload(artifact)
    payload["comparable_to_registered_backtest"] = True
    path = tmp_path / "unsafe-comparability-receipt.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        report_event_paper_observation.ObservationInputError,
        match="publication receipt is invalid",
    ):
        report_event_paper_observation.load_and_validate_publish_receipt(
            path,
            artifact=artifact,
        )


@pytest.mark.parametrize("published", [[], None])
def test_validate_receipt_requires_exact_candidate_coverage(
    tmp_path: Path,
    published: list[dict[str, Any]] | None,
) -> None:
    artifact = _write_artifact(tmp_path)
    if published is None:
        record = _published_record(artifact=artifact)
        record["execution_candidate_id"] = "cluster-extra:obs-extra"
        published = [record]
    receipt = EventPaperPublishReceipt.model_validate(
        _receipt_payload(artifact, published=published)
    )

    with pytest.raises(
        report_event_paper_observation.ObservationInputError,
        match="coverage mismatch",
    ):
        report_event_paper_observation.validate_publish_receipt(
            artifact=artifact,
            receipt=receipt,
        )


def test_validate_receipt_rejects_duplicate_occurrence(tmp_path: Path) -> None:
    artifact = _write_artifact(tmp_path)
    record = _published_record(artifact=artifact)
    receipt = EventPaperPublishReceipt.model_validate(
        _receipt_payload(artifact, published=[record, copy.deepcopy(record)])
    )

    with pytest.raises(
        report_event_paper_observation.ObservationInputError,
        match="duplicate execution_candidate_id",
    ):
        report_event_paper_observation.validate_publish_receipt(
            artifact=artifact,
            receipt=receipt,
        )


def test_fetch_and_build_report_reconciles_exact_open_position(tmp_path: Path) -> None:
    artifact = _write_artifact(tmp_path)
    receipt = _receipt(artifact)
    strategy_signal_id = receipt.published[0].signal_id
    requests: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        params = dict(request.url.params)
        if path == "/rest/v1/strategy_logs":
            assert params["signal_id"] == f"in.({strategy_signal_id})"
            return httpx.Response(
                200,
                json=[_strategy_log_row(artifact, receipt)],
            )
        if path == "/rest/v1/aggregator_logs":
            assert params["strategy_signal_id_a"] == f"in.({strategy_signal_id})"
            return httpx.Response(
                200,
                json=[
                    {
                        "signal_id": "22222222-2222-2222-2222-222222222222",
                        "symbol": "7203",
                        "action": "BUY",
                        "confidence": 0.5,
                        "signal_source": "RULE",
                        "strategy_signal_id_a": strategy_signal_id,
                        "strategy_signal_id_b": None,
                        "created_at": "2026-01-21T00:01:01+00:00",
                    }
                ],
            )
        if path == "/rest/v1/trades_paper":
            if "unified_signal_id" in params:
                assert params["unified_signal_id"] == ("in.(22222222-2222-2222-2222-222222222222)")
                assert "symbol" not in params
                return httpx.Response(
                    200,
                    json=[
                        {
                            "trade_id": "trade-buy",
                            "order_id": "order-buy",
                            "symbol": "7203",
                            "side": "BUY",
                            "quantity": 100,
                            "price": "1023",
                            "signal_source": "RULE",
                            "unified_signal_id": ("22222222-2222-2222-2222-222222222222"),
                            "executed_at": "2026-01-21T00:02:00+00:00",
                        }
                    ],
                )
            assert params["symbol"] == "in.(7203)"
            assert params["executed_at"].startswith("gte.")
            return httpx.Response(200, json=[])
        if path == "/rest/v1/positions":
            assert params["trade_type"] == "eq.paper"
            assert params["symbol"] == "in.(7203)"
            return httpx.Response(
                200,
                json=[
                    {
                        "symbol": "7203",
                        "trade_type": "paper",
                        "side": "LONG",
                        "quantity": 100,
                        "entry_price": "1023",
                        "current_price": "1030",
                        "unrealized_pnl": "700",
                        "holding_type": "swing",
                        "stop_loss_price": "920.70",
                        "max_hold_days": 20,
                        "scheduled_exit_date": "2026-02-18",
                        "opened_at": "2026-01-21T00:02:00+00:00",
                    }
                ],
            )
        return httpx.Response(404)

    with httpx.Client(
        base_url="https://example.supabase.co",
        transport=httpx.MockTransport(_handler),
    ) as client:
        rows = report_event_paper_observation.fetch_supabase_rows(
            client,
            artifact=artifact.artifact,
            receipt=receipt,
        )

    report = report_event_paper_observation.build_report(
        artifact.artifact,
        receipt=receipt,
        rows=rows,
    )

    assert [request.url.path for request in requests].count("/rest/v1/trades_paper") == 2
    assert report["summary"]["status_counts"] == {"open_position": 1}
    row = report["rows"][0]
    assert row["strategy_log_found"] is True
    assert row["aggregator_log_found"] is True
    assert row["buy_trade_id"] == "trade-buy"
    assert row["position_open"] is True
    assert row["position_stop_loss_price"] == "920.70"
    assert row["position_scheduled_exit_date"] == "2026-02-18"
    assert row["observed_ask"] == "1000"
    assert report_event_paper_observation.Decimal(row["entry_slippage_bps"]) == 230


def test_same_symbol_unlinked_trade_does_not_satisfy_event_fill(tmp_path: Path) -> None:
    artifact = _write_artifact(tmp_path)
    receipt = _receipt(artifact)
    strategy_signal_id = receipt.published[0].signal_id
    unified_signal_id = "22222222-2222-2222-2222-222222222222"
    rows = report_event_paper_observation.SupabaseRows(
        strategy_logs=[_strategy_log_row(artifact, receipt)],
        aggregator_logs=[
            {
                "signal_id": unified_signal_id,
                "strategy_signal_id_a": strategy_signal_id,
                "signal_source": "RULE",
                "symbol": "7203",
                "action": "BUY",
            }
        ],
        trades_paper=[
            {
                "trade_id": "unrelated-buy",
                "symbol": "7203",
                "side": "BUY",
                "price": "1000",
                "signal_source": "RULE",
                "unified_signal_id": "33333333-3333-3333-3333-333333333333",
                "executed_at": "2026-01-21T00:02:00+00:00",
            }
        ],
        positions=[
            {
                "symbol": "7203",
                "opened_at": "2026-01-21T00:02:00+00:00",
            }
        ],
    )

    report = report_event_paper_observation.build_report(
        artifact.artifact,
        receipt=receipt,
        rows=rows,
    )

    assert report["summary"]["status_counts"] == {"missing_buy_fill": 1}
    assert report["rows"][0]["buy_trade_id"] is None
    assert report["rows"][0]["position_open"] is False


def test_strategy_claim_must_match_receipt_lineage(tmp_path: Path) -> None:
    artifact = _write_artifact(tmp_path)
    receipt = _receipt(artifact)
    strategy_row = _strategy_log_row(artifact, receipt)
    reasoning = json.loads(str(strategy_row["reasoning"]))
    reasoning["artifact_sha256"] = "0" * 64
    strategy_row["reasoning"] = json.dumps(reasoning)
    rows = report_event_paper_observation.SupabaseRows(
        strategy_logs=[strategy_row],
        aggregator_logs=[],
        trades_paper=[],
        positions=[],
    )

    report = report_event_paper_observation.build_report(
        artifact.artifact,
        receipt=receipt,
        rows=rows,
    )

    assert report["rows"][0]["strategy_log_found"] is False
    assert report["rows"][0]["reconciliation_status"] == "missing_strategy_log"


def test_strategy_claim_attempt_must_match_receipt(tmp_path: Path) -> None:
    artifact = _write_artifact(tmp_path)
    receipt = _receipt(artifact)
    strategy_row = _strategy_log_row(artifact, receipt)
    reasoning = json.loads(str(strategy_row["reasoning"]))
    reasoning["publication_attempt"]["attempt_id"] = "different-attempt"
    reasoning["publication"]["attempt_id"] = "different-attempt"
    strategy_row["reasoning"] = json.dumps(reasoning)
    rows = report_event_paper_observation.SupabaseRows(
        strategy_logs=[strategy_row],
        aggregator_logs=[],
        trades_paper=[],
        positions=[],
    )

    report = report_event_paper_observation.build_report(
        artifact.artifact,
        receipt=receipt,
        rows=rows,
    )

    assert report["rows"][0]["strategy_log_found"] is False
    assert report["rows"][0]["reconciliation_status"] == "missing_strategy_log"


def test_null_unified_swing_sell_after_linked_buy_is_attributed(tmp_path: Path) -> None:
    artifact = _write_artifact(tmp_path)
    receipt = _receipt(artifact)
    strategy_signal_id = receipt.published[0].signal_id
    unified_signal_id = "22222222-2222-2222-2222-222222222222"
    rows = report_event_paper_observation.SupabaseRows(
        strategy_logs=[_strategy_log_row(artifact, receipt)],
        aggregator_logs=[
            {
                "signal_id": unified_signal_id,
                "strategy_signal_id_a": strategy_signal_id,
                "signal_source": "RULE",
                "symbol": "7203",
                "action": "BUY",
            }
        ],
        trades_paper=[
            {
                "trade_id": "event-buy",
                "symbol": "7203",
                "side": "BUY",
                "price": "1000",
                "signal_source": "RULE",
                "unified_signal_id": unified_signal_id,
                "executed_at": "2026-01-21T00:02:00+00:00",
            },
            {
                "trade_id": "scheduled-or-stop-sell",
                "symbol": "7203",
                "side": "SELL",
                "price": "1100",
                "signal_source": "CONSENSUS",
                "unified_signal_id": None,
                "executed_at": "2026-02-18T00:03:00+00:00",
            },
        ],
        positions=[],
    )

    report = report_event_paper_observation.build_report(
        artifact.artifact,
        receipt=receipt,
        rows=rows,
    )

    row = report["rows"][0]
    assert row["sell_trade_id"] == "scheduled-or-stop-sell"
    assert row["reconciliation_status"] == "closed_or_exited"


def test_symbol_sell_after_next_buy_generation_is_not_attributed(tmp_path: Path) -> None:
    artifact = _write_artifact(tmp_path)
    receipt = _receipt(artifact)
    strategy_signal_id = receipt.published[0].signal_id
    unified_signal_id = "22222222-2222-2222-2222-222222222222"
    rows = report_event_paper_observation.SupabaseRows(
        strategy_logs=[_strategy_log_row(artifact, receipt)],
        aggregator_logs=[
            {
                "signal_id": unified_signal_id,
                "strategy_signal_id_a": strategy_signal_id,
                "signal_source": "RULE",
                "symbol": "7203",
                "action": "BUY",
            }
        ],
        trades_paper=[
            {
                "trade_id": "event-buy",
                "symbol": "7203",
                "side": "BUY",
                "price": "1000",
                "signal_source": "RULE",
                "unified_signal_id": unified_signal_id,
                "executed_at": "2026-01-21T00:02:00+00:00",
            },
            {
                "trade_id": "later-generation-buy",
                "symbol": "7203",
                "side": "BUY",
                "price": "1050",
                "signal_source": "RULE",
                "unified_signal_id": "33333333-3333-3333-3333-333333333333",
                "executed_at": "2026-03-01T00:02:00+00:00",
            },
            {
                "trade_id": "later-generation-sell",
                "symbol": "7203",
                "side": "SELL",
                "price": "1060",
                "signal_source": "CONSENSUS",
                "unified_signal_id": None,
                "executed_at": "2026-03-02T00:03:00+00:00",
            },
        ],
        positions=[],
    )

    report = report_event_paper_observation.build_report(
        artifact.artifact,
        receipt=receipt,
        rows=rows,
    )

    row = report["rows"][0]
    assert row["sell_trade_id"] is None
    assert row["reconciliation_status"] == "no_open_position_no_sell"


def test_position_from_later_generation_is_not_attributed(tmp_path: Path) -> None:
    artifact = _write_artifact(tmp_path)
    receipt = _receipt(artifact)
    strategy_signal_id = receipt.published[0].signal_id
    unified_signal_id = "22222222-2222-2222-2222-222222222222"
    rows = report_event_paper_observation.SupabaseRows(
        strategy_logs=[_strategy_log_row(artifact, receipt)],
        aggregator_logs=[
            {
                "signal_id": unified_signal_id,
                "strategy_signal_id_a": strategy_signal_id,
                "signal_source": "RULE",
                "symbol": "7203",
                "action": "BUY",
            }
        ],
        trades_paper=[
            {
                "trade_id": "event-buy",
                "symbol": "7203",
                "side": "BUY",
                "price": "1000",
                "signal_source": "RULE",
                "unified_signal_id": unified_signal_id,
                "executed_at": "2026-01-21T00:02:00+00:00",
            }
        ],
        positions=[
            {
                "symbol": "7203",
                "opened_at": "2026-03-01T00:02:00+00:00",
            }
        ],
    )

    report = report_event_paper_observation.build_report(
        artifact.artifact,
        receipt=receipt,
        rows=rows,
    )

    assert report["rows"][0]["position_open"] is False
    assert report["rows"][0]["reconciliation_status"] == "no_open_position_no_sell"


def test_cli_writes_candidate_only_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    artifact = _write_artifact(tmp_path)
    output_json = tmp_path / "report.json"
    output_csv = tmp_path / "report.csv"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "report-event-paper-observation.py",
            "--candidates-json",
            str(artifact.source_path),
            "--output-json",
            str(output_json),
            "--output-csv",
            str(output_csv),
            "--skip-supabase",
        ],
    )

    assert report_event_paper_observation.main() == 0

    report = json.loads(output_json.read_text(encoding="utf-8"))
    assert report["summary"]["status_counts"] == {"dry_run_only": 1}
    assert "execution_candidate_id" in output_csv.read_text(encoding="utf-8")


def test_cli_consumes_separate_publication_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _write_artifact(tmp_path)
    receipt_path = tmp_path / "publish-receipt.json"
    receipt_path.write_text(
        json.dumps(_receipt_payload(artifact), ensure_ascii=False),
        encoding="utf-8",
    )
    output_json = tmp_path / "report.json"
    output_csv = tmp_path / "report.csv"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "report-event-paper-observation.py",
            "--candidates-json",
            str(artifact.source_path),
            "--publish-receipt-json",
            str(receipt_path),
            "--output-json",
            str(output_json),
            "--output-csv",
            str(output_csv),
            "--skip-supabase",
        ],
    )

    assert report_event_paper_observation.main() == 0

    report = json.loads(output_json.read_text(encoding="utf-8"))
    assert report["summary"]["publication_receipt_loaded"] is True
    assert report["summary"]["status_counts"] == {"published_unqueried": 1}
    assert report["rows"][0]["observed_ask"] == "1000"
    csv_text = output_csv.read_text(encoding="utf-8")
    assert "publication_status" in csv_text
    assert "publication_attempt_id" in csv_text
    assert "attempted_at" in csv_text
    assert "comparable_to_registered_backtest" in csv_text
