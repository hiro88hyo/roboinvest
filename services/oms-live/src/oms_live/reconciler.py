"""kabu ``/positions`` と Supabase ``positions(live)`` の整合性チェック。

OMS Live の write path は ``trades_live`` 経由の約定だけを ``positions`` に反映するため、
個人取引や別経路 (kabu ステーションアプリ手動発注など) で発生した建玉は Supabase 側に
乗らない。本モジュールは両ソースを取って差分を分類する純関数を提供する。

設計指針 (memory ``positions_integrity.md``):

- kabu にあって Supabase に無い → ``to_import`` (manual 建玉として取り込み候補)
- 両方にあるが quantity が違う → ``quantity_mismatches`` (warning)
- Supabase にあって kabu に無い → ``supabase_orphans`` (warning、**強制削除はしない**)
- 両方にあって quantity 一致 → ``matched`` (no-op)

**強制同期 (Supabase 側からの DELETE) は行わない**。個人保有を巻き込む事故になる。
取り込み (``to_import`` の適用) も明示的な ``--apply`` フラグでのみ実行する想定。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from trade_contracts.enums import TradingStyle

from .clients.supabase import SupabaseClient
from .models import LivePosition

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class KabuPositionSnapshot:
    """kabu ``/positions`` 1 行を内部正規化したもの。

    ``quantity`` は ``LeavesQty + HoldQty`` の合計。kabu の現物建玉では
    ``LeavesQty`` が「売却可能な保有数量」、``HoldQty`` が「売り注文中の拘束数量」を
    表すので、真の保有株数はその和になる。
    """

    symbol: str
    quantity: int
    average_price: Decimal
    current_price: Decimal | None = None
    unrealized_pnl: Decimal | None = None


@dataclass(frozen=True, slots=True)
class QuantityMismatch:
    symbol: str
    kabu_quantity: int
    supabase_quantity: int
    kabu_average_price: Decimal
    supabase_entry_price: Decimal
    kabu_current_price: Decimal | None = None
    kabu_unrealized_pnl: Decimal | None = None


@dataclass(frozen=True, slots=True)
class ReconcileActions:
    """``compute_position_diff`` の戻り値。"""

    to_import: tuple[KabuPositionSnapshot, ...]
    quantity_mismatches: tuple[QuantityMismatch, ...]
    supabase_orphans: tuple[LivePosition, ...]
    matched: tuple[str, ...]

    @property
    def has_drift(self) -> bool:
        return bool(self.to_import or self.quantity_mismatches or self.supabase_orphans)


def parse_kabu_position(row: dict[str, Any]) -> KabuPositionSnapshot | None:
    """kabu ``/positions`` の 1 行を ``KabuPositionSnapshot`` にパースする。

    以下の場合は ``None`` を返してスキップ:

    - ``Side != "2"`` (現物の買建以外、空売は LONG 限定なので無視)
    - 合計数量 (``LeavesQty + HoldQty``) が 0 以下
    - 必須フィールドが欠損 / 不正

    パース失敗 (例外) はログに残して ``None``。
    """
    try:
        symbol = str(row["Symbol"])
        side = str(row.get("Side", ""))
        leaves = int(row.get("LeavesQty") or 0)
        hold = int(row.get("HoldQty") or 0)
        price_raw = row.get("Price")
        if price_raw is None:
            logger.warning("kabu position skipped (no Price): symbol=%s", symbol)
            return None
        average_price = Decimal(str(price_raw))
        current_price = _optional_decimal(row.get("CurrentPrice"))
        unrealized_pnl = _optional_decimal(row.get("ProfitLoss"))
    except (KeyError, TypeError, ValueError, ArithmeticError):
        logger.exception("kabu position parse failed: row=%r", row)
        return None

    if side != "2":
        logger.info("kabu position skipped (non-buy side): symbol=%s side=%s", symbol, side)
        return None

    quantity = leaves + hold
    if quantity <= 0:
        logger.info(
            "kabu position skipped (zero qty): symbol=%s leaves=%d hold=%d",
            symbol,
            leaves,
            hold,
        )
        return None

    return KabuPositionSnapshot(
        symbol=symbol,
        quantity=quantity,
        average_price=average_price,
        current_price=current_price,
        unrealized_pnl=unrealized_pnl,
    )


def _optional_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (TypeError, ValueError, ArithmeticError):
        return None


def parse_kabu_positions(rows: Iterable[dict[str, Any]]) -> list[KabuPositionSnapshot]:
    """``parse_kabu_position`` を複数行に適用 (None は除外)。"""
    out: list[KabuPositionSnapshot] = []
    for row in rows:
        parsed = parse_kabu_position(row)
        if parsed is not None:
            out.append(parsed)
    return out


def compute_position_diff(
    kabu_positions: Iterable[KabuPositionSnapshot],
    supabase_positions: Iterable[LivePosition],
) -> ReconcileActions:
    """両ソースを (symbol, quantity) で突合し ``ReconcileActions`` を返す純関数。

    重複 symbol は kabu 側で起きないはず (同一銘柄は 1 行に集約される) だが、
    万一来た場合は最後に出てきた行で上書きする。Supabase 側は ``(symbol, trade_type)``
    複合 PK で一意。
    """
    kabu_by_symbol: dict[str, KabuPositionSnapshot] = {p.symbol: p for p in kabu_positions}
    sup_by_symbol: dict[str, LivePosition] = {p.symbol: p for p in supabase_positions}

    to_import: list[KabuPositionSnapshot] = []
    mismatches: list[QuantityMismatch] = []
    orphans: list[LivePosition] = []
    matched: list[str] = []

    for symbol, kabu in kabu_by_symbol.items():
        sup = sup_by_symbol.get(symbol)
        if sup is None:
            to_import.append(kabu)
            continue
        if kabu.quantity != sup.quantity:
            mismatches.append(
                QuantityMismatch(
                    symbol=symbol,
                    kabu_quantity=kabu.quantity,
                    supabase_quantity=sup.quantity,
                    kabu_average_price=kabu.average_price,
                    supabase_entry_price=sup.entry_price,
                    kabu_current_price=kabu.current_price,
                    kabu_unrealized_pnl=kabu.unrealized_pnl,
                )
            )
            continue
        matched.append(symbol)

    for symbol, sup in sup_by_symbol.items():
        if symbol not in kabu_by_symbol:
            orphans.append(sup)

    return ReconcileActions(
        to_import=tuple(to_import),
        quantity_mismatches=tuple(mismatches),
        supabase_orphans=tuple(orphans),
        matched=tuple(matched),
    )


def build_imported_position(
    snapshot: KabuPositionSnapshot,
    *,
    now: datetime,
    holding_type: TradingStyle = TradingStyle.SWING,
) -> LivePosition:
    """``to_import`` の 1 件を ``LivePosition`` に変換する純関数。

    取り込んだ建玉は **判定不能** な属性 (holding_type / max_hold_days / trailing 等) を
    持たない。デフォルトは:

    - ``holding_type=swing`` (期限なしポジションとして扱い、14:50 day closeout を回避)
    - ``max_hold_days=None`` (期限なし)
    - ``target_price`` / ``stop_loss_price`` / ``trailing_stop_pct`` は未設定

    取り込み後はユーザが Dashboard で個別調整する想定。``opened_at`` は reconcile 実行
    時刻 (``now``) を使う (本来の建玉日時は kabu API で得られない)。
    """
    return LivePosition(
        symbol=snapshot.symbol,
        quantity=snapshot.quantity,
        entry_price=snapshot.average_price,
        holding_type=holding_type,
        target_price=None,
        stop_loss_price=None,
        max_hold_days=None,
        trailing_stop_pct=None,
        opened_at=now,
    )


async def apply_reconcile_actions(
    actions: ReconcileActions,
    supabase: SupabaseClient,
    *,
    now: datetime,
    apply_imports: bool = False,
    holding_type: TradingStyle = TradingStyle.SWING,
) -> int:
    """``actions`` のうち ``to_import`` を Supabase に反映する I/O 適用層。

    ``apply_imports=False`` (デフォルト) では何もせず ``0`` を返す。warning を出すのみ。
    ``apply_imports=True`` のときに限り ``insert_live_position`` を呼ぶ。
    ``quantity_mismatches`` / ``supabase_orphans`` は **常に warning ログのみ** で、
    Supabase は触らない (個人保有を巻き込まないための明示的な制約)。

    戻り値は実際に INSERT した件数。
    """
    for mismatch in actions.quantity_mismatches:
        logger.warning(
            "position quantity mismatch (no auto-fix): symbol=%s kabu_qty=%d supabase_qty=%d "
            "kabu_avg=%s supabase_entry=%s",
            mismatch.symbol,
            mismatch.kabu_quantity,
            mismatch.supabase_quantity,
            mismatch.kabu_average_price,
            mismatch.supabase_entry_price,
        )
    for orphan in actions.supabase_orphans:
        logger.warning(
            "supabase position not in kabu (no auto-delete): symbol=%s qty=%d entry=%s",
            orphan.symbol,
            orphan.quantity,
            orphan.entry_price,
        )

    if not actions.to_import:
        return 0

    if not apply_imports:
        for snap in actions.to_import:
            logger.warning(
                "kabu position not in supabase (dry-run, no import): symbol=%s qty=%d avg=%s",
                snap.symbol,
                snap.quantity,
                snap.average_price,
            )
        return 0

    inserted = 0
    for snap in actions.to_import:
        position = build_imported_position(snap, now=now, holding_type=holding_type)
        await supabase.insert_live_position(position)
        logger.info(
            "imported manual position: symbol=%s qty=%d entry=%s holding_type=%s",
            position.symbol,
            position.quantity,
            position.entry_price,
            position.holding_type.value,
        )
        inserted += 1
    return inserted


async def apply_quantity_mismatch_fixes(
    actions: ReconcileActions,
    supabase: SupabaseClient,
    *,
    symbols: set[str] | None = None,
) -> int:
    """quantity mismatch を明示 opt-in で Supabase 側へ反映する。

    ``scripts/reconcile-positions.py --fix-quantity-mismatch`` からのみ使う運用補助。
    kabu 実残を信頼して ``quantity`` / ``entry_price`` を合わせ、kabu が返す場合は
    ``current_price`` / ``unrealized_pnl`` も同時に更新する。orphan DELETE はしない。
    """
    fixed = 0
    for mismatch in actions.quantity_mismatches:
        if symbols is not None and mismatch.symbol not in symbols:
            continue
        await supabase.update_live_position_reconciled(
            symbol=mismatch.symbol,
            quantity=mismatch.kabu_quantity,
            entry_price=str(mismatch.kabu_average_price),
            current_price=str(mismatch.kabu_current_price)
            if mismatch.kabu_current_price is not None
            else None,
            unrealized_pnl=str(mismatch.kabu_unrealized_pnl)
            if mismatch.kabu_unrealized_pnl is not None
            else None,
        )
        logger.warning(
            "fixed quantity mismatch from kabu: symbol=%s qty %d->%d entry %s->%s",
            mismatch.symbol,
            mismatch.supabase_quantity,
            mismatch.kabu_quantity,
            mismatch.supabase_entry_price,
            mismatch.kabu_average_price,
        )
        fixed += 1
    return fixed


__all__ = [
    "KabuPositionSnapshot",
    "QuantityMismatch",
    "ReconcileActions",
    "apply_quantity_mismatch_fixes",
    "apply_reconcile_actions",
    "build_imported_position",
    "compute_position_diff",
    "parse_kabu_position",
    "parse_kabu_positions",
]
