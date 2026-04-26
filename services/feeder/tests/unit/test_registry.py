"""``streaming.registry`` の純関数テスト。"""

from __future__ import annotations

from feeder.kabu_client import SymbolRegistration
from feeder.streaming.registry import compute_diff, to_registrations


def test_to_registrations_applies_default_exchange() -> None:
    regs = to_registrations(["7203", "9984"], default_exchange=1)

    assert regs == frozenset(
        {
            SymbolRegistration(symbol="7203", exchange=1),
            SymbolRegistration(symbol="9984", exchange=1),
        }
    )


def test_to_registrations_drops_empty_strings() -> None:
    assert to_registrations(["7203", "", "9984"], default_exchange=1) == frozenset(
        {
            SymbolRegistration(symbol="7203", exchange=1),
            SymbolRegistration(symbol="9984", exchange=1),
        }
    )


def test_to_registrations_dedupes() -> None:
    regs = to_registrations(["7203", "7203", "9984"], default_exchange=1)
    assert len(regs) == 2


def test_compute_diff_initial_register() -> None:
    desired = to_registrations(["7203", "9984"], default_exchange=1)
    diff = compute_diff(current=frozenset(), desired=desired)

    assert [r.symbol for r in diff.add] == ["7203", "9984"]
    assert diff.remove == ()
    assert diff.is_empty is False


def test_compute_diff_no_change_returns_empty() -> None:
    desired = to_registrations(["7203", "9984"], default_exchange=1)
    diff = compute_diff(current=desired, desired=desired)

    assert diff.add == ()
    assert diff.remove == ()
    assert diff.is_empty is True


def test_compute_diff_partial_swap() -> None:
    current = to_registrations(["7203", "9984", "6758"], default_exchange=1)
    desired = to_registrations(["9984", "6758", "8035"], default_exchange=1)

    diff = compute_diff(current=current, desired=desired)

    assert [r.symbol for r in diff.add] == ["8035"]
    assert [r.symbol for r in diff.remove] == ["7203"]


def test_compute_diff_handles_different_exchanges_as_distinct() -> None:
    current = frozenset({SymbolRegistration(symbol="7203", exchange=1)})
    desired = frozenset({SymbolRegistration(symbol="7203", exchange=2)})

    diff = compute_diff(current=current, desired=desired)

    assert diff.add == (SymbolRegistration(symbol="7203", exchange=2),)
    assert diff.remove == (SymbolRegistration(symbol="7203", exchange=1),)


def test_compute_diff_orders_are_stable() -> None:
    desired = to_registrations(["9984", "6758", "8035", "7203"], default_exchange=1)
    diff = compute_diff(current=frozenset(), desired=desired)

    # symbol 昇順で安定
    assert [r.symbol for r in diff.add] == ["6758", "7203", "8035", "9984"]
