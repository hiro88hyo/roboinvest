"""kabuステーション ``/register`` 状態の差分管理。純関数のみ。

session.py が watchlist スナップショットを受け取るたびに ``compute_diff``
を呼んで「追加分」「削除分」を取り出し、kabu API へそれぞれ
``/register`` / ``/unregister`` を投げる。

設計メモ:
- watchlist には ``symbol`` しか無いため、Feeder は env の
  ``KABU_DEFAULT_EXCHANGE`` を使って ``SymbolRegistration`` を組む
- 同一 ``(symbol, exchange)`` の重複は ``frozenset`` で吸収
- ``add`` / ``remove`` の順序は安定 (symbol 昇順) — テストの再現性確保
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ..kabu_client import SymbolRegistration


@dataclass(frozen=True, slots=True)
class RegistrationDiff:
    """``current`` から ``desired`` への移行に必要な kabu API 操作。"""

    add: tuple[SymbolRegistration, ...]
    remove: tuple[SymbolRegistration, ...]

    @property
    def is_empty(self) -> bool:
        return not self.add and not self.remove


def to_registrations(
    symbols: Iterable[str], *, default_exchange: int
) -> frozenset[SymbolRegistration]:
    """``symbol`` 文字列のイテラブルを ``SymbolRegistration`` 集合に変換。"""
    return frozenset(SymbolRegistration(symbol=s, exchange=default_exchange) for s in symbols if s)


def compute_diff(
    *,
    current: Iterable[SymbolRegistration],
    desired: Iterable[SymbolRegistration],
) -> RegistrationDiff:
    """``current`` 集合を ``desired`` に揃えるための add / remove を返す。

    どちらの引数もイテラブルで OK。内部で集合化するため重複は吸収される。
    出力は ``(symbol, exchange)`` の昇順で安定化する。
    """
    current_set = frozenset(current)
    desired_set = frozenset(desired)

    add = tuple(sorted(desired_set - current_set, key=_sort_key))
    remove = tuple(sorted(current_set - desired_set, key=_sort_key))
    return RegistrationDiff(add=add, remove=remove)


def _sort_key(reg: SymbolRegistration) -> tuple[str, int]:
    return (reg.symbol, reg.exchange)
