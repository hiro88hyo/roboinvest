"""Phase 1 統合バリデータ(純関数)。

``UnifiedTradeSignal`` に対してキルスイッチ + action 判定 + ロット計算を
一括で評価する。I/O・時刻依存・外部状態なし。

``existing_long_quantity`` は Phase 3 で Supabase から取得するポジション数量の注入口。
Phase 1 の単体テストでは呼び出し側がそのまま値を渡す。
"""

from __future__ import annotations

from decimal import Decimal

from trade_contracts.enums import Action, TradeMode
from trade_contracts.risk import KillSwitchState, RiskCheck
from trade_contracts.signal import UnifiedTradeSignal

from . import kill_switch, lot_calculator
from .config import RiskConfig


def validate(
    *,
    signal: UnifiedTradeSignal,
    state: KillSwitchState,
    risk_config: RiskConfig,
    entry_price: Decimal,
    existing_long_quantity: int | None = None,
) -> RiskCheck:
    """シグナルを受け入れ可否判定する。

    順序:
      1. ``action == HOLD`` → reject (``action_hold``)
      2. kill_switch 評価 → reject ならそのまま返す
      3. BUY:
         - 既存 LONG が存在 (``existing_long_quantity > 0``) → reject (``already_long``)
         - それ以外は ``lot_calculator.calculate`` に委譲
      4. SELL:
         - 既存 LONG なし (``None`` または ``0``) → reject (``no_position_for_sell``)
         - あれば ``adjusted_quantity = existing_long_quantity`` で passed
    """

    if signal.action is Action.HOLD:
        return RiskCheck(passed=False, reason="action_hold")

    ks = kill_switch.evaluate(state)
    if not ks.passed:
        return ks

    if signal.action is Action.BUY:
        if signal.stop_loss_pct is not None and state.trade_mode is TradeMode.LIVE:
            return RiskCheck(passed=False, reason="relative_stop_live_unsupported")
        if existing_long_quantity is not None and existing_long_quantity > 0:
            return RiskCheck(passed=False, reason="already_long")
        return lot_calculator.calculate(
            signal=signal,
            entry_price=entry_price,
            config=risk_config,
        )

    if signal.action is Action.SELL:
        if existing_long_quantity is None or existing_long_quantity <= 0:
            return RiskCheck(passed=False, reason="no_position_for_sell")
        return RiskCheck(passed=True, adjusted_quantity=existing_long_quantity)

    raise AssertionError(f"unexpected action: {signal.action}")
