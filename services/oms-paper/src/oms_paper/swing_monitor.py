"""Phase 4 swing position 自動決済の判定 (純関数)。

`positions.holding_type=swing` の paper ポジションに対して、最新価格と現在時刻
だけを入力に取り、以下のいずれかの処置を返す:

* 成行 SELL で決済 (``stop_loss`` / ``target`` / ``max_hold_days``)
* トレーリングストップで ``stop_loss_price`` を切り上げ
* 何もしない

I/O・時刻取得・板アクセス・Supabase 書込はここでは行わない。
``streaming/runner.py`` 側が ``OrderBookSnapshot`` から最新価格を抽出し、
``wall_clock`` で ``now`` を渡す。

判定優先順位 (ルート CLAUDE.md "スイングトレード管理"節と一致):

1. ``stop_loss_price`` を下回る → ``exit reason='stop_loss'``
2. ``target_price`` 以上 → ``exit reason='target'``
3. ``max_hold_days`` 経過 → ``exit reason='max_hold_days'``
4. ``trailing_stop_pct`` 設定下で stop が切り上げ可能 → ``trail``
5. 上記のいずれにも該当しない → ``hold``

Trailing は **単調増加** のみ (既存 stop を下げない)。
``PaperPosition`` には高値履歴を持たないので、最新 tick 価格を実効的な
高値とみなして毎回 ``new_stop = max(latest * (1 - pct), existing)`` で再計算する。
これは保守的で、stop は単調増加のみとなる。
"""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from trade_contracts.enums import TradingStyle

from .models import PaperPosition, SwingDecision

_PRICE_QUANT = Decimal("1")
_HOLD = SwingDecision(action="hold")


def evaluate_swing_exit(
    *,
    position: PaperPosition,
    latest_price: Decimal,
    now: datetime,
) -> SwingDecision:
    """swing ポジションの自動決済判定。

    ``holding_type != swing`` のポジション、および swing でも全閾値が
    未設定のポジションは ``hold`` を返す (短絡)。
    """

    if position.holding_type is not TradingStyle.SWING:
        return _HOLD

    if position.stop_loss_price is not None and latest_price <= position.stop_loss_price:
        return SwingDecision(action="exit", reason="stop_loss")

    if position.target_price is not None and latest_price >= position.target_price:
        return SwingDecision(action="exit", reason="target")

    if position.max_hold_days is not None:
        elapsed_days = (now.date() - position.opened_at.date()).days
        if elapsed_days >= position.max_hold_days:
            return SwingDecision(action="exit", reason="max_hold_days")

    if position.trailing_stop_pct is not None:
        candidate = (latest_price * (Decimal(1) - position.trailing_stop_pct)).quantize(
            _PRICE_QUANT, rounding=ROUND_HALF_UP
        )
        existing = position.stop_loss_price
        if existing is None or candidate > existing:
            return SwingDecision(action="trail", new_stop_loss_price=candidate)

    return _HOLD
