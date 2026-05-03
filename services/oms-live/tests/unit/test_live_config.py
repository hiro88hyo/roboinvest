"""``OmsLiveSettings`` の単体テスト (Phase 3 安全装備の env パース)。"""

from __future__ import annotations

from oms_live.config import OmsLiveSettings


def _settings(**overrides: object) -> OmsLiveSettings:
    return OmsLiveSettings(**overrides)  # type: ignore[arg-type]


def test_allowed_symbol_set_is_empty_by_default() -> None:
    assert _settings().allowed_symbol_set == frozenset()


def test_allowed_symbol_set_parses_csv() -> None:
    s = _settings(oms_live_allowed_symbols="7203,9984,8035")
    assert s.allowed_symbol_set == frozenset({"7203", "9984", "8035"})


def test_allowed_symbol_set_strips_whitespace() -> None:
    s = _settings(oms_live_allowed_symbols=" 7203 , 9984  ,  ")
    assert s.allowed_symbol_set == frozenset({"7203", "9984"})


def test_allowed_symbol_set_treats_blank_string_as_empty() -> None:
    s = _settings(oms_live_allowed_symbols="   ")
    assert s.allowed_symbol_set == frozenset()


def test_max_qty_per_order_defaults_to_none() -> None:
    assert _settings().oms_live_max_qty_per_order is None


def test_dry_run_defaults_to_false() -> None:
    assert _settings().oms_live_dry_run is False
