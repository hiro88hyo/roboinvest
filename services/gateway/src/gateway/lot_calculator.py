"""Phase 1 ロット計算(2% ルール、純関数)。

``UnifiedTradeSignal`` とエントリー価格・資金パラメータから、
許容損失 (= 資金 x ``max_risk_per_trade_pct``) を超えない最大株数を算出し、
``min_lot_size`` 単位に floor する。

BUY 専用のユーティリティ。SELL は既存ポジションの全決済なので Phase 3 の
呼び出し側(validator 経由)で別経路にする。
"""

from __future__ import annotations

from decimal import Decimal

from trade_contracts.enums import Action, TradingStyle
from trade_contracts.risk import RiskCheck
from trade_contracts.signal import UnifiedTradeSignal

from .config import RiskConfig


def _resolve_stop_loss_for_buy(
    *,
    entry_price: Decimal,
    explicit_stop: Decimal | None,
    default_spread_pct: Decimal,
) -> Decimal:
    if explicit_stop is not None:
        return explicit_stop
    return entry_price * (Decimal("1") - default_spread_pct)


def calculate(
    *,
    signal: UnifiedTradeSignal,
    entry_price: Decimal,
    config: RiskConfig,
) -> RiskCheck:
    """BUY シグナルに対して 2% ルールでロット数を算出する。

    * ``signal.action`` は ``BUY`` 前提(呼び出し側で保証する)。
    * ``entry_price`` は正。0 以下は invalid_entry_price で reject。
    * ``stop_loss_price`` が ``None`` の場合は ``default_stop_loss_spread_pct`` で代替。
    * BUY なので ``stop_loss >= entry_price`` は無意味 → invalid_stop_loss で reject。
    * ``holding_type=swing`` のときは ``swing_risk_scale`` で保守側にスケール。
    * ``min_lot_size`` 単位に floor し、それ未満は below_min_lot で reject。
    """

    if signal.action is not Action.BUY:
        raise ValueError(
            f"lot_calculator.calculate only supports BUY signals, got: {signal.action}"
        )

    if entry_price <= 0:
        return RiskCheck(passed=False, reason="invalid_entry_price")

    stop_loss = _resolve_stop_loss_for_buy(
        entry_price=entry_price,
        explicit_stop=signal.stop_loss_price,
        default_spread_pct=config.default_stop_loss_spread_pct,
    )
    if stop_loss >= entry_price:
        return RiskCheck(passed=False, reason="invalid_stop_loss")

    risk_per_share = entry_price - stop_loss
    if risk_per_share <= 0:
        return RiskCheck(passed=False, reason="invalid_stop_loss")

    scale = config.swing_risk_scale if signal.holding_type is TradingStyle.SWING else Decimal("1")
    risk_amount = config.capital * config.max_risk_per_trade_pct * scale
    if risk_amount <= 0:
        return RiskCheck(passed=False, reason="invalid_risk_config")

    raw_qty = risk_amount / risk_per_share
    # Decimal を int に落とす際は truncate(ROUND_DOWN 相当、保守側)。
    floored_shares = int(raw_qty)
    adjusted = (floored_shares // config.min_lot_size) * config.min_lot_size
    if config.max_notional_per_order_pct is not None:
        max_notional = config.capital * config.max_notional_per_order_pct
        if max_notional <= 0:
            return RiskCheck(passed=False, reason="invalid_risk_config")
        notional_cap_shares = int(max_notional / entry_price)
        notional_cap_adjusted = (notional_cap_shares // config.min_lot_size) * config.min_lot_size
        adjusted = min(adjusted, notional_cap_adjusted)
    if adjusted < config.min_lot_size:
        return RiskCheck(passed=False, reason="below_min_lot")

    return RiskCheck(passed=True, adjusted_quantity=adjusted)
