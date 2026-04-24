"""Phase 1 キルスイッチ評価(純関数)。

``system_status`` のスナップショット (``KillSwitchState``) を受け取り、
``RiskCheck`` を返す。副作用なし・I/O なし。
"""

from __future__ import annotations

from trade_contracts.enums import TradeMode
from trade_contracts.risk import KillSwitchState, RiskCheck


def evaluate(state: KillSwitchState) -> RiskCheck:
    """キルスイッチ・損失上限を評価する。

    * ``is_trading_allowed=False`` なら即座に reject。
    * ``trade_mode=paper`` のときは pnl 系チェックをスキップ(資金リスクがないため)。
    * 日次 / 週次 / 月次 のいずれかが損失上限に達していたら reject。
      ``pnl <= -loss_limit`` を境界として reject 側に倒す(fail-closed)。
    """

    if not state.is_trading_allowed:
        return RiskCheck(passed=False, reason="kill_switch_off")

    if state.trade_mode is TradeMode.PAPER:
        return RiskCheck(passed=True)

    if state.daily_pnl <= -state.daily_loss_limit:
        return RiskCheck(passed=False, reason="daily_loss_limit")
    if state.weekly_pnl <= -state.weekly_loss_limit:
        return RiskCheck(passed=False, reason="weekly_loss_limit")
    if state.monthly_pnl <= -state.monthly_loss_limit:
        return RiskCheck(passed=False, reason="monthly_loss_limit")

    return RiskCheck(passed=True)
