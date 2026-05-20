from __future__ import annotations

from collections.abc import Iterable


def normalize_symbol_code(raw: object) -> str:
    """Normalize J-Quants symbol codes to broker-facing instrument codes."""
    code = str(raw or "").strip()
    if len(code) == 5 and code.endswith("0"):
        return code[:-1]
    return code


def collect_legacy_symbol_codes(raw_codes: Iterable[object]) -> list[str]:
    """Collect distinct pre-normalized symbol codes that differ from current output."""
    legacy_codes: set[str] = set()
    for raw in raw_codes:
        code = str(raw or "").strip()
        if code and code != normalize_symbol_code(code):
            legacy_codes.add(code)
    return sorted(legacy_codes)
