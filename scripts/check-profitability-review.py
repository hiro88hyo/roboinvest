#!/usr/bin/env python3
"""Validate the tracked profitability review evidence ledger."""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REVIEW_ROOT = REPO_ROOT / "docs" / "review" / "profitability"
LEDGER = REVIEW_ROOT / "evidence-ledger.csv"

REQUIRED_REVIEW_FILES = (
    "README.md",
    "EVIDENCE.md",
    "METHODOLOGY.md",
    "REPRODUCIBILITY.md",
    "SOURCE_MAP.md",
    "evidence-ledger.csv",
)
REQUIRED_COLUMNS = {
    "evidence_id",
    "evidence_class",
    "period",
    "mode",
    "strategy",
    "closed_trades",
    "pnl_jpy",
    "pnl_semantics",
    "profit_factor",
    "max_drawdown_jpy",
    "decision",
    "source_path",
    "limitation",
}
ALLOWED_CLASSES = {"operations", "replay", "research"}
ALLOWED_MODES = {"live", "paper", "paper_replay", "backtest"}
ALLOWED_DECISIONS = {
    "context_only",
    "fail_gate",
    "rejected",
    "research_only",
    "paper_observation_only",
    "observation_only",
    "no_candidates",
    "inconclusive_detection",
}
ALLOWED_PNL_SEMANTICS = {
    "reported_realized",
    "gross_execution",
    "cost_adjusted",
    "no_trades",
}
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def validate_review_package() -> list[str]:
    errors: list[str] = []
    for name in REQUIRED_REVIEW_FILES:
        if not (REVIEW_ROOT / name).is_file():
            errors.append(f"missing review file: docs/review/profitability/{name}")

    for markdown_path in REVIEW_ROOT.glob("*.md"):
        _validate_markdown_links(errors, markdown_path)

    if not LEDGER.is_file():
        return errors

    with LEDGER.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        columns = set(reader.fieldnames or ())
        missing_columns = REQUIRED_COLUMNS - columns
        if missing_columns:
            errors.append(f"ledger missing columns: {','.join(sorted(missing_columns))}")
            return errors
        rows = list(reader)

    if not rows:
        errors.append("evidence ledger is empty")
        return errors

    seen_ids: set[str] = set()
    for line_number, row in enumerate(rows, start=2):
        evidence_id = row["evidence_id"].strip()
        if not evidence_id:
            errors.append(f"line {line_number}: empty evidence_id")
        elif evidence_id in seen_ids:
            errors.append(f"line {line_number}: duplicate evidence_id={evidence_id}")
        seen_ids.add(evidence_id)

        _validate_enum(errors, line_number, row, "evidence_class", ALLOWED_CLASSES)
        _validate_enum(errors, line_number, row, "mode", ALLOWED_MODES)
        _validate_enum(errors, line_number, row, "decision", ALLOWED_DECISIONS)
        _validate_enum(errors, line_number, row, "pnl_semantics", ALLOWED_PNL_SEMANTICS)

        source_path = row["source_path"].strip()
        if not source_path:
            errors.append(f"line {line_number}: empty source_path")
        elif Path(source_path).is_absolute():
            errors.append(f"line {line_number}: source_path must be repository-relative")
        elif not (REPO_ROOT / source_path).is_file():
            errors.append(f"line {line_number}: missing source_path={source_path}")

        if not row["limitation"].strip():
            errors.append(f"line {line_number}: limitation is required")

        for field in ("closed_trades", "pnl_jpy", "profit_factor", "max_drawdown_jpy"):
            _validate_number(errors, line_number, row, field)

    return errors


def _validate_markdown_links(errors: list[str], markdown_path: Path) -> None:
    text = markdown_path.read_text(encoding="utf-8")
    for target in MARKDOWN_LINK_RE.findall(text):
        if target.startswith(("http://", "https://", "#")):
            continue
        relative_target = target.split("#", 1)[0]
        if not relative_target:
            continue
        resolved = (markdown_path.parent / relative_target).resolve()
        if not resolved.exists():
            path = markdown_path.relative_to(REPO_ROOT)
            errors.append(f"broken markdown link: {path} -> {target}")


def _validate_enum(
    errors: list[str],
    line_number: int,
    row: dict[str, str],
    field: str,
    allowed: set[str],
) -> None:
    value = row[field].strip()
    if value not in allowed:
        errors.append(f"line {line_number}: invalid {field}={value!r}")


def _validate_number(errors: list[str], line_number: int, row: dict[str, str], field: str) -> None:
    value = row[field].strip()
    if not value:
        return
    try:
        float(value)
    except ValueError:
        errors.append(f"line {line_number}: invalid numeric {field}={value!r}")


def main() -> int:
    errors = validate_review_package()
    if errors:
        for error in errors:
            print(f"NG {error}")
        return 1
    print("OK profitability review package")
    return 0


if __name__ == "__main__":
    sys.exit(main())
