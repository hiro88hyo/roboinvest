import csv
import importlib.util
import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from strategy_rule.event_paper.artifact import EventArtifactError, load_event_paper_artifact


def _load_module():
    path = Path(__file__).resolve().parents[1] / "detect-event-cluster-paper-candidates.py"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("detect_event_cluster_paper_candidates", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


detect_event_cluster_paper_candidates = _load_module()


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def _write_completed_financial_fetch(
    path: Path,
    rows: list[dict[str, object]],
    *,
    target_date: date,
    fetched_at: str,
) -> None:
    target_fetched_at = datetime.fromisoformat(fetched_at).astimezone(UTC).isoformat()
    tagged: list[dict[str, object]] = []
    target_count = 0
    for row in rows:
        disclosed_date = date.fromisoformat(str(row.get("DiscDate") or row["DisclosedDate"]))
        row_fetched_at = (
            target_fetched_at
            if disclosed_date == target_date
            else datetime.combine(
                disclosed_date,
                datetime.min.time(),
                tzinfo=UTC,
            )
            .replace(hour=7)
            .isoformat()
        )
        tagged.append({**row, "_roboinvest_fetched_at": row_fetched_at})
        if disclosed_date == target_date:
            target_count += 1
    tagged.append(
        {
            "_roboinvest_record_type": "fetch_metadata",
            "_roboinvest_target_date": target_date.isoformat(),
            "_roboinvest_fetched_at": target_fetched_at,
            "_roboinvest_row_count": target_count,
        }
    )
    _write_jsonl(path, tagged)


def _write_ohlcv(
    path: Path,
    *,
    end_date: date | None = None,
    future_open_offset: int = 1,
) -> None:
    start = date(2026, 1, 1)
    rows = []
    for idx in range(55):
        current = start + timedelta(days=idx)
        if end_date is not None and current > end_date:
            continue
        close = 1000 + idx
        rows.append(
            {
                "symbol": "7203",
                "date": current.isoformat(),
                "open": close + (future_open_offset if current > date(2026, 1, 21) else 1),
                "high": close + 10,
                "low": close - 10,
                "close": close,
                "volume": 500000,
                "turnover": close * 500000,
            }
        )
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["symbol", "date", "open", "high", "low", "close", "volume", "turnover"],
        )
        writer.writeheader()
        writer.writerows(rows)


def _write_one_week_ohlcv(path: Path) -> None:
    trading_dates = [
        date(2026, 1, 5) + timedelta(days=idx)
        for idx in range(40)
        if (date(2026, 1, 5) + timedelta(days=idx)).weekday() < 5
    ]
    rows = []
    for symbol_idx, symbol in enumerate(("7203", "6501", "9984")):
        base = 1000 + symbol_idx * 250
        for idx, current in enumerate(trading_dates):
            close = base + idx
            rows.append(
                {
                    "symbol": symbol,
                    "date": current.isoformat(),
                    "open": close + 1,
                    "high": close + 10,
                    "low": close - 10,
                    "close": close,
                    "volume": 500000,
                    "turnover": close * 500000,
                }
            )
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["symbol", "date", "open", "high", "low", "close", "volume", "turnover"],
        )
        writer.writeheader()
        writer.writerows(rows)


def _summary_row(
    *,
    code: str,
    disc_date: str,
    disc_no: str,
    doc_type: str,
    feps: str = "100",
    fdiv: str = "40",
) -> dict[str, object]:
    return {
        "Code": code,
        "DiscDate": disc_date,
        "DiscTime": "15:30:00",
        "DiscNo": disc_no,
        "DocType": doc_type,
        "CurFYEn": "2026-03-31",
        "FEPS": feps,
        "FOP": "1000",
        "FNP": "900",
        "FSales": "10000",
        "EPS": "90",
        "BPS": "1000",
        "FDivAnn": fdiv,
    }


def _summary_rows() -> list[dict[str, object]]:
    return [
        {
            "Code": "72030",
            "DiscDate": "2026-01-20",
            "DiscTime": "15:30:00",
            "DiscNo": "prior-forecast",
            "DocType": "ForecastRevision_Consolidated_JP",
            "CurFYEn": "2026-03-31",
            "FEPS": "100",
            "FOP": "1000",
            "FNP": "900",
            "FSales": "10000",
            "EPS": "90",
            "BPS": "1000",
            "FDivAnn": "40",
        },
        {
            "Code": "72030",
            "DiscDate": "2026-01-21",
            "DiscTime": "15:30:00",
            "DiscNo": "earnings-current",
            "DocType": "3QFinancialStatements_Consolidated_JP",
            "CurFYEn": "2026-03-31",
            "FEPS": "120",
            "FOP": "1200",
            "FNP": "1000",
            "FSales": "11000",
            "EPS": "95",
            "BPS": "1000",
            "FDivAnn": "50",
        },
        {
            "Code": "72030",
            "DiscDate": "2026-01-21",
            "DiscTime": "15:30:00",
            "DiscNo": "dividend-current",
            "DocType": "DividendRevision_Consolidated_JP",
            "CurFYEn": "2026-03-31",
            "FEPS": "120",
            "FOP": "1200",
            "FNP": "1000",
            "FSales": "11000",
            "EPS": "95",
            "BPS": "1000",
            "FDivAnn": "50",
        },
    ]


def _one_week_summary_rows() -> list[dict[str, object]]:
    return [
        _summary_row(
            code="72030",
            disc_date="2026-01-19",
            disc_no="7203-prior",
            doc_type="ForecastRevision_Consolidated_JP",
            feps="100",
            fdiv="40",
        ),
        _summary_row(
            code="72030",
            disc_date="2026-01-20",
            disc_no="7203-earnings",
            doc_type="3QFinancialStatements_Consolidated_JP",
            feps="120",
            fdiv="50",
        ),
        _summary_row(
            code="72030",
            disc_date="2026-01-20",
            disc_no="7203-dividend",
            doc_type="DividendRevision_Consolidated_JP",
            feps="120",
            fdiv="50",
        ),
        _summary_row(
            code="65010",
            disc_date="2026-01-20",
            disc_no="6501-prior",
            doc_type="ForecastRevision_Consolidated_JP",
            feps="100",
            fdiv="30",
        ),
        _summary_row(
            code="65010",
            disc_date="2026-01-21",
            disc_no="6501-earnings-high-per",
            doc_type="3QFinancialStatements_Consolidated_JP",
            feps="30",
            fdiv="45",
        ),
        _summary_row(
            code="65010",
            disc_date="2026-01-21",
            disc_no="6501-dividend-high-per",
            doc_type="DividendRevision_Consolidated_JP",
            feps="30",
            fdiv="45",
        ),
        _summary_row(
            code="99840",
            disc_date="2026-01-21",
            disc_no="9984-prior",
            doc_type="ForecastRevision_Consolidated_JP",
            feps="100",
            fdiv="10",
        ),
        _summary_row(
            code="99840",
            disc_date="2026-01-22",
            disc_no="9984-earnings-missing-per",
            doc_type="3QFinancialStatements_Consolidated_JP",
            feps="",
            fdiv="20",
        ),
        _summary_row(
            code="99840",
            disc_date="2026-01-22",
            disc_no="9984-dividend-missing-per",
            doc_type="DividendRevision_Consolidated_JP",
            feps="",
            fdiv="20",
        ),
        _summary_row(
            code="72030",
            disc_date="2026-01-23",
            disc_no="7203-earnings-only",
            doc_type="3QFinancialStatements_Consolidated_JP",
            feps="130",
            fdiv="50",
        ),
    ]


def _run_single_day_detection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    suffix: str,
    ohlcv_end_date: date | None,
    future_open_offset: int = 1,
    fetched_at: str = "2026-01-21T15:30:00+00:00",
) -> dict[str, object]:
    financial_path = tmp_path / f"financial-{suffix}.jsonl"
    ohlcv_path = tmp_path / f"ohlcv-{suffix}.csv"
    output_json = tmp_path / f"candidates-{suffix}.json"
    output_csv = tmp_path / f"candidates-{suffix}.csv"
    _write_completed_financial_fetch(
        financial_path,
        _summary_rows(),
        target_date=date(2026, 1, 21),
        fetched_at=fetched_at,
    )
    _write_ohlcv(
        ohlcv_path,
        end_date=ohlcv_end_date,
        future_open_offset=future_open_offset,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "detect-event-cluster-paper-candidates.py",
            "--financial-summary-jsonl",
            str(financial_path),
            "--ohlcv",
            str(ohlcv_path),
            "--output-json",
            str(output_json),
            "--output-csv",
            str(output_csv),
            "--signal-date",
            "2026-01-21",
        ],
    )
    assert detect_event_cluster_paper_candidates.main() == 0
    return json.loads(output_json.read_text(encoding="utf-8"))


def test_detect_cluster_v1_candidates_dry_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    financial_path = tmp_path / "financial.jsonl"
    ohlcv_path = tmp_path / "ohlcv.csv"
    output_json = tmp_path / "candidates.json"
    output_csv = tmp_path / "candidates.csv"
    _write_completed_financial_fetch(
        financial_path,
        _summary_rows(),
        target_date=date(2026, 1, 21),
        fetched_at="2026-01-21T15:30:00+00:00",
    )
    _write_ohlcv(ohlcv_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "detect-event-cluster-paper-candidates.py",
            "--financial-summary-jsonl",
            str(financial_path),
            "--ohlcv",
            str(ohlcv_path),
            "--output-json",
            str(output_json),
            "--output-csv",
            str(output_csv),
            "--signal-date",
            "2026-01-21",
        ],
    )

    assert detect_event_cluster_paper_candidates.main() == 0

    payload = json.loads(output_json.read_text(encoding="utf-8"))
    loaded = load_event_paper_artifact(output_json)
    assert loaded.artifact.candidates[0].symbol == "7203"
    assert payload["schema_version"] == 3
    assert payload["strategy_key"] == (
        "event_cluster_earnings_dividend_value_guard_fixed20_stop_v1_research"
    )
    assert payload["mode"] == "dry_run"
    assert payload["publish_enabled"] is False
    assert payload["causality_verified"] is True
    assert payload["causality"]["receipt_provenance"] == "export_metadata"
    assert payload["rule"]["catastrophic_stop_pct"] == "-0.10"
    assert payload["summary"]["candidate_count"] == 1
    candidate = payload["candidates"][0]
    assert candidate["candidate_id"] == (
        "event_cluster_earnings_dividend_value_guard_fixed20_stop_v1_research"
    )
    assert candidate["execution_candidate_id"] == (
        f"{candidate['cluster_id']}:{candidate['observation_id']}"
    )
    assert candidate["symbol"] == "7203"
    assert candidate["has_earnings_result"] is True
    assert candidate["has_dividend_increase"] is True
    assert candidate["max_hold_days"] == 20
    assert candidate["valuation_reference_price"] == "1020"
    assert candidate["data_available_at"] == "2026-01-21T06:30:00+00:00"
    assert candidate["feature_cutoff_at"] == "2026-01-21T06:30:00+00:00"
    assert candidate["source_received_at"] == "2026-01-21T15:30:00+00:00"
    assert candidate["feature_data_complete"] is True
    assert candidate["selection_status"] == "eligible"
    assert candidate["required_ohlcv_session_date"] == "2026-01-21"
    assert candidate["catastrophic_stop_pct"] == "-0.10"
    assert candidate["entry_price_status"] == "unresolved_until_fresh_market_observation"
    assert "entry_price_assumption" not in candidate
    assert "stop_loss_price" not in candidate
    assert candidate["publish_ready"] is False
    csv_text = output_csv.read_text(encoding="utf-8")
    assert "execution_candidate_id" in csv_text
    assert "source_received_at" in csv_text
    assert "publish_ready" in csv_text
    assert "False" in csv_text


def test_detector_preserves_disclosure_time_per_vintage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    financial_path = tmp_path / "financial-early-disclosure.jsonl"
    ohlcv_path = tmp_path / "ohlcv-early-disclosure.csv"
    output_json = tmp_path / "candidates-early-disclosure.json"
    output_csv = tmp_path / "candidates-early-disclosure.csv"
    rows = _summary_rows()
    for row in rows:
        if row["DiscDate"] == "2026-01-21":
            row["DiscTime"] = "14:00:00"
            row["FEPS"] = "100"
    _write_completed_financial_fetch(
        financial_path,
        rows,
        target_date=date(2026, 1, 21),
        fetched_at="2026-01-21T15:30:00+00:00",
    )
    _write_ohlcv(ohlcv_path)
    with ohlcv_path.open(encoding="utf-8") as handle:
        ohlcv_rows = list(csv.DictReader(handle))
    for row in ohlcv_rows:
        if row["date"] == "2026-01-20":
            row.update(open="1400", high="1410", low="1390", close="1400")
        elif row["date"] == "2026-01-21":
            row.update(open="1600", high="1610", low="1590", close="1600")
    with ohlcv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=ohlcv_rows[0].keys())
        writer.writeheader()
        writer.writerows(ohlcv_rows)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "detect-event-cluster-paper-candidates.py",
            "--financial-summary-jsonl",
            str(financial_path),
            "--ohlcv",
            str(ohlcv_path),
            "--output-json",
            str(output_json),
            "--output-csv",
            str(output_csv),
            "--signal-date",
            "2026-01-21",
        ],
    )

    assert detect_event_cluster_paper_candidates.main() == 0
    payload = json.loads(output_json.read_text(encoding="utf-8"))
    load_event_paper_artifact(output_json)

    [candidate] = payload["candidates"]
    assert candidate["min_forecast_per"] == "14"
    assert candidate["valuation_reference_price"] == "1400"
    assert candidate["valuation_reference_bar_date"] == "2026-01-20"
    assert candidate["feature_cutoff_at"] == "2026-01-21T05:00:00+00:00"
    assert candidate["source_received_at"] == "2026-01-21T15:30:00+00:00"


def test_candidate_can_be_detected_without_entry_day_ohlcv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _run_single_day_detection(
        tmp_path,
        monkeypatch,
        suffix="through-signal-date",
        ohlcv_end_date=date(2026, 1, 21),
    )

    assert payload["causality_verified"] is True
    assert payload["summary"]["event_count"] == 2
    assert payload["summary"]["candidate_count"] == 1
    candidate = payload["candidates"][0]
    assert candidate["entry_date"] == "2026-01-22"
    assert candidate["valuation_reference_bar_date"] == "2026-01-21"
    assert "entry_price_assumption" not in candidate


def test_candidate_artifact_is_invariant_to_future_bar_removal_and_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    truncated = _run_single_day_detection(
        tmp_path,
        monkeypatch,
        suffix="truncated",
        ohlcv_end_date=date(2026, 1, 21),
    )
    mutated_future = _run_single_day_detection(
        tmp_path,
        monkeypatch,
        suffix="mutated-future",
        ohlcv_end_date=None,
        future_open_offset=50_000,
    )

    assert truncated["candidates"] == mutated_future["candidates"]
    assert truncated["exclusions"] == mutated_future["exclusions"]


def test_late_jquants_receipt_is_recorded_and_excluded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _run_single_day_detection(
        tmp_path,
        monkeypatch,
        suffix="late-receipt",
        ohlcv_end_date=date(2026, 1, 21),
        fetched_at="2026-01-22T01:00:00+00:00",
    )

    assert payload["causality_verified"] is False
    assert payload["summary"]["candidate_count"] == 0
    assert payload["summary"]["late_data_receipt_count"] == 2
    assert {row["reason"] for row in payload["exclusions"]} == {"late_data_receipt"}


def test_missing_signal_date_ohlcv_preserves_selection_but_blocks_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _run_single_day_detection(
        tmp_path,
        monkeypatch,
        suffix="missing-signal-bar",
        ohlcv_end_date=date(2026, 1, 20),
    )

    assert payload["causality_verified"] is True
    assert payload["summary"]["candidate_count"] == 1
    assert payload["summary"]["missing_required_ohlcv_session_count"] == 1
    assert payload["exclusions"] == []
    [candidate] = payload["candidates"]
    assert candidate["selection_status"] == "incomplete_required_ohlcv_session"
    assert candidate["required_ohlcv_session_date"] == "2026-01-21"
    assert candidate["valuation_reference_price"] is None
    assert candidate["valuation_reference_bar_date"] is None
    assert candidate["valuation_reference_available_at"] is None
    assert candidate["min_forecast_per"] is None
    assert candidate["feature_data_complete"] is False

    artifact_path = tmp_path / "candidates-missing-signal-bar.json"
    artifact = load_event_paper_artifact(artifact_path).artifact
    with pytest.raises(EventArtifactError, match="feature data is incomplete"):
        artifact.validate_target_date(date(2026, 1, 22))


def test_missing_intraday_required_bar_preserves_cohort_without_using_d2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    financial_path = tmp_path / "financial-missing-intraday-required.jsonl"
    ohlcv_path = tmp_path / "ohlcv-missing-intraday-required.csv"
    output_json = tmp_path / "candidates-missing-intraday-required.json"
    output_csv = tmp_path / "candidates-missing-intraday-required.csv"
    rows = _summary_rows()
    for row in rows:
        if row["DiscDate"] == "2026-01-21":
            row["DiscTime"] = "14:00:00"
    _write_completed_financial_fetch(
        financial_path,
        rows,
        target_date=date(2026, 1, 21),
        fetched_at="2026-01-21T15:30:00+00:00",
    )
    _write_ohlcv(ohlcv_path, end_date=date(2026, 1, 21))
    with ohlcv_path.open(encoding="utf-8") as handle:
        ohlcv_rows = [row for row in csv.DictReader(handle) if row["date"] != "2026-01-20"]
    for row in ohlcv_rows:
        if row["date"] == "2026-01-19":
            row.update(open="9999", high="10009", low="9989", close="9999")
    with ohlcv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=ohlcv_rows[0].keys())
        writer.writeheader()
        writer.writerows(ohlcv_rows)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "detect-event-cluster-paper-candidates.py",
            "--financial-summary-jsonl",
            str(financial_path),
            "--ohlcv",
            str(ohlcv_path),
            "--output-json",
            str(output_json),
            "--output-csv",
            str(output_csv),
            "--signal-date",
            "2026-01-21",
        ],
    )

    assert detect_event_cluster_paper_candidates.main() == 0

    payload = json.loads(output_json.read_text(encoding="utf-8"))
    [candidate] = payload["candidates"]
    assert payload["summary"]["candidate_count"] == 1
    assert payload["summary"]["missing_required_ohlcv_session_count"] == 1
    assert candidate["required_ohlcv_session_date"] == "2026-01-20"
    assert candidate["feature_data_complete"] is False
    assert candidate["selection_status"] == "incomplete_required_ohlcv_session"
    assert candidate["valuation_reference_price"] is None
    assert candidate["valuation_reference_bar_date"] is None
    assert candidate["valuation_reference_available_at"] is None
    assert candidate["min_forecast_per"] is None
    assert candidate["event_id"] in candidate["event_ids"]
    assert len(candidate["event_ids"]) == 2
    assert "9999" not in json.dumps(candidate)
    artifact = load_event_paper_artifact(output_json).artifact
    assert artifact.candidates[0].feature_data_complete is False


@pytest.mark.parametrize(
    ("fetched_at", "expected_verified"),
    [
        ("2026-01-21T07:00:00+00:00", False),
        ("2026-01-21T15:30:00+00:00", True),
        ("2026-01-22T01:00:00+00:00", False),
    ],
)
def test_zero_row_fetch_requires_complete_source_coverage_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fetched_at: str,
    expected_verified: bool,
) -> None:
    financial_path = tmp_path / "financial-empty.jsonl"
    ohlcv_path = tmp_path / "ohlcv-empty.csv"
    output_json = tmp_path / "candidates-empty.json"
    output_csv = tmp_path / "candidates-empty.csv"
    _write_completed_financial_fetch(
        financial_path,
        [],
        target_date=date(2026, 1, 21),
        fetched_at=fetched_at,
    )
    _write_ohlcv(ohlcv_path, end_date=date(2026, 1, 21))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "detect-event-cluster-paper-candidates.py",
            "--financial-summary-jsonl",
            str(financial_path),
            "--ohlcv",
            str(ohlcv_path),
            "--output-json",
            str(output_json),
            "--output-csv",
            str(output_csv),
            "--signal-date",
            "2026-01-21",
        ],
    )

    assert detect_event_cluster_paper_candidates.main() == 0

    payload = json.loads(output_json.read_text(encoding="utf-8"))
    assert payload["causality_verified"] is expected_verified
    assert payload["causality"]["source_coverage_window_verified"] is expected_verified
    assert payload["summary"]["event_count"] == 0
    assert payload["summary"]["candidate_count"] == 0


def test_unmarked_empty_archive_is_not_causally_verified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    financial_path = tmp_path / "financial-unmarked-empty.jsonl"
    ohlcv_path = tmp_path / "ohlcv-unmarked-empty.csv"
    output_json = tmp_path / "candidates-unmarked-empty.json"
    output_csv = tmp_path / "candidates-unmarked-empty.csv"
    financial_path.write_text("", encoding="utf-8")
    _write_ohlcv(ohlcv_path, end_date=date(2026, 1, 21))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "detect-event-cluster-paper-candidates.py",
            "--financial-summary-jsonl",
            str(financial_path),
            "--ohlcv",
            str(ohlcv_path),
            "--output-json",
            str(output_json),
            "--output-csv",
            str(output_csv),
            "--signal-date",
            "2026-01-21",
            "--fetched-at",
            "2026-01-21T07:00:00+00:00",
        ],
    )

    assert detect_event_cluster_paper_candidates.main() == 0

    payload = json.loads(output_json.read_text(encoding="utf-8"))
    assert payload["causality_verified"] is False
    assert payload["causality"]["receipt_provenance"] == "explicit_cli_unverified"


def test_latest_completion_marker_can_repair_an_older_malformed_marker() -> None:
    rows = [
        {
            "_roboinvest_record_type": "fetch_metadata",
            "_roboinvest_target_date": "2026-01-21",
        },
        {
            "Code": "72030",
            "DiscDate": "2026-01-21",
            "_roboinvest_fetched_at": "2026-01-21T15:30:00+00:00",
        },
        {
            "_roboinvest_record_type": "fetch_metadata",
            "_roboinvest_target_date": "2026-01-21",
            "_roboinvest_fetched_at": "2026-01-21T15:30:00+00:00",
            "_roboinvest_row_count": 1,
        },
    ]

    fetched_at, complete = detect_event_cluster_paper_candidates.validated_fetch_metadata(
        rows,
        signal_date=date(2026, 1, 21),
    )

    assert fetched_at == datetime(2026, 1, 21, 15, 30, tzinfo=UTC)
    assert complete is True


def test_malformed_latest_completion_marker_is_not_verified() -> None:
    rows = [
        {
            "_roboinvest_record_type": "fetch_metadata",
            "_roboinvest_target_date": "2026-01-21",
            "_roboinvest_fetched_at": "2026-01-21T15:30:00+00:00",
            "_roboinvest_row_count": 0,
        },
        {
            "_roboinvest_record_type": "fetch_metadata",
            "_roboinvest_target_date": "2026-01-21",
            "_roboinvest_fetched_at": "not-a-timestamp",
            "_roboinvest_row_count": 0,
        },
    ]

    fetched_at, complete = detect_event_cluster_paper_candidates.validated_fetch_metadata(
        rows,
        signal_date=date(2026, 1, 21),
    )

    assert fetched_at is None
    assert complete is False


def test_one_week_detection_fixture_matches_research_rule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    financial_path = tmp_path / "financial-one-week.jsonl"
    ohlcv_path = tmp_path / "ohlcv-one-week.csv"
    output_json = tmp_path / "candidates-one-week.json"
    output_csv = tmp_path / "candidates-one-week.csv"
    _write_jsonl(financial_path, _one_week_summary_rows())
    _write_one_week_ohlcv(ohlcv_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "detect-event-cluster-paper-candidates.py",
            "--financial-summary-jsonl",
            str(financial_path),
            "--ohlcv",
            str(ohlcv_path),
            "--output-json",
            str(output_json),
            "--output-csv",
            str(output_csv),
            "--fetched-at",
            "2026-01-23T07:00:00+00:00",
        ],
    )

    assert detect_event_cluster_paper_candidates.main() == 0

    payload = json.loads(output_json.read_text(encoding="utf-8"))
    assert payload["mode"] == "dry_run"
    assert payload["publish_enabled"] is False
    assert payload["paper_live_enabled"] is False
    assert payload["rule"]["catastrophic_stop_pct"] == "-0.10"
    assert payload["summary"] == {
        "event_count": 10,
        "observation_count": 10,
        "late_data_receipt_count": 0,
        "fetched_before_disclosure_count": 0,
        "missing_required_ohlcv_session_count": 0,
        "missing_feature_history_count": 0,
        "candidate_count": 2,
        "exclusion_count": 1,
        "published_count": 0,
    }
    assert [(row["symbol"], row["signal_date"]) for row in payload["candidates"]] == [
        ("7203", "2026-01-20"),
        ("9984", "2026-01-22"),
    ]
    assert payload["candidates"][0]["min_forecast_per"] == "8.425"
    assert payload["candidates"][0]["entry_price_status"] == (
        "unresolved_until_fresh_market_observation"
    )
    assert payload["candidates"][1]["min_forecast_per"] is None
    assert [
        (row["symbol"], row["signal_date"], row["reason"], row["min_forecast_per"])
        for row in payload["exclusions"]
    ] == [("6501", "2026-01-21", "forecast_per_value_guard", "42.06666666666666666666666667")]


def test_signal_date_filter_keeps_one_day_from_one_week_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    financial_path = tmp_path / "financial-one-week.jsonl"
    ohlcv_path = tmp_path / "ohlcv-one-week.csv"
    output_json = tmp_path / "candidates-filtered.json"
    output_csv = tmp_path / "candidates-filtered.csv"
    _write_completed_financial_fetch(
        financial_path,
        _one_week_summary_rows(),
        target_date=date(2026, 1, 20),
        fetched_at="2026-01-20T15:30:00+00:00",
    )
    _write_one_week_ohlcv(ohlcv_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "detect-event-cluster-paper-candidates.py",
            "--financial-summary-jsonl",
            str(financial_path),
            "--ohlcv",
            str(ohlcv_path),
            "--output-json",
            str(output_json),
            "--output-csv",
            str(output_csv),
            "--signal-date",
            "2026-01-20",
        ],
    )

    assert detect_event_cluster_paper_candidates.main() == 0

    payload = json.loads(output_json.read_text(encoding="utf-8"))
    assert payload["signal_date"] == "2026-01-20"
    assert payload["summary"] == {
        "event_count": 3,
        "observation_count": 3,
        "late_data_receipt_count": 0,
        "fetched_before_disclosure_count": 0,
        "missing_required_ohlcv_session_count": 0,
        "missing_feature_history_count": 0,
        "candidate_count": 1,
        "exclusion_count": 0,
        "published_count": 0,
    }
    assert [(row["symbol"], row["signal_date"]) for row in payload["candidates"]] == [
        ("7203", "2026-01-20")
    ]


def test_main_publish_is_disabled_until_execution_intent_is_causal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    financial_path = tmp_path / "financial.jsonl"
    ohlcv_path = tmp_path / "ohlcv.csv"
    output_json = tmp_path / "candidates.json"
    output_csv = tmp_path / "candidates.csv"
    _write_jsonl(financial_path, _summary_rows())
    _write_ohlcv(ohlcv_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "detect-event-cluster-paper-candidates.py",
            "--financial-summary-jsonl",
            str(financial_path),
            "--ohlcv",
            str(ohlcv_path),
            "--output-json",
            str(output_json),
            "--output-csv",
            str(output_csv),
            "--signal-date",
            "2026-01-21",
            "--publish-paper",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        detect_event_cluster_paper_candidates.main()

    message = str(exc_info.value)
    assert "event paper publish is disabled" in message
    assert "strategy-rule event-paper-publish" in message
    assert not output_json.exists()
