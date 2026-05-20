from universe_scanner.symbols import collect_legacy_symbol_codes, normalize_symbol_code


def test_normalize_symbol_code_removes_jquants_v2_trailing_market_suffix() -> None:
    assert normalize_symbol_code("72030") == "7203"
    assert normalize_symbol_code("278A0") == "278A"


def test_normalize_symbol_code_leaves_other_codes_unchanged() -> None:
    assert normalize_symbol_code("7203") == "7203"
    assert normalize_symbol_code("1306") == "1306"
    assert normalize_symbol_code("") == ""


def test_collect_legacy_symbol_codes_returns_only_pre_normalized_variants() -> None:
    assert collect_legacy_symbol_codes(["72030", "7203", "278A0", "278A", ""]) == [
        "278A0",
        "72030",
    ]
