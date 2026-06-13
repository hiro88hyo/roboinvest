from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from math import sqrt

from pydantic import BaseModel

_SLIPPAGE_RATE = Decimal("0.0005")
_TAX_RATE = Decimal("0.20315")


class ClosedTrade(BaseModel):
    symbol: str
    quantity: int
    entry_price: Decimal
    exit_price: Decimal
    entry_notional: Decimal
    exit_notional: Decimal
    gross_pnl: Decimal
    commission: Decimal
    net_pnl_before_tax: Decimal
    executed_at: datetime


class BacktestReport(BaseModel):
    closed_trade_count: int
    total_gross_pnl: Decimal
    total_commission: Decimal
    total_slippage: Decimal
    tax: Decimal
    total_net_pnl: Decimal
    win_rate: Decimal
    profit_factor: Decimal | None
    max_drawdown: Decimal
    sharpe_ratio: Decimal | None
    expectancy: Decimal


def build_backtest_report(closed_trades: list[ClosedTrade]) -> BacktestReport:
    """閉じたトレード列から収益指標を作る。

    手数料は runner 側で約定代金 0.099% として ClosedTrade に入っている。
    この report では追加で entry/exit 約定代金それぞれ 0.05% の一律
    スリッページと、集計利益が正のときだけ 20.315% の税を控除する。
    """

    trade_pnls_before_tax: list[Decimal] = []
    total_gross = Decimal("0")
    total_commission = Decimal("0")
    total_slippage = Decimal("0")

    for trade in closed_trades:
        notional = trade.entry_notional + trade.exit_notional
        slippage = notional * _SLIPPAGE_RATE
        pnl = trade.net_pnl_before_tax - slippage
        trade_pnls_before_tax.append(pnl)
        total_gross += trade.gross_pnl
        total_commission += trade.commission
        total_slippage += slippage

    total_before_tax = sum(trade_pnls_before_tax, Decimal("0"))
    tax = total_before_tax * _TAX_RATE if total_before_tax > 0 else Decimal("0")
    total_net = total_before_tax - tax
    closed_count = len(trade_pnls_before_tax)
    wins = sum(1 for pnl in trade_pnls_before_tax if pnl > 0)
    losses = [pnl for pnl in trade_pnls_before_tax if pnl < 0]
    gains = [pnl for pnl in trade_pnls_before_tax if pnl > 0]
    gross_profit = sum(gains, Decimal("0"))
    gross_loss = abs(sum(losses, Decimal("0")))

    return BacktestReport(
        closed_trade_count=closed_count,
        total_gross_pnl=total_gross,
        total_commission=total_commission,
        total_slippage=total_slippage,
        tax=tax,
        total_net_pnl=total_net,
        win_rate=_ratio(Decimal(wins), Decimal(closed_count)),
        profit_factor=None if gross_loss == 0 else gross_profit / gross_loss,
        max_drawdown=_max_drawdown(trade_pnls_before_tax),
        sharpe_ratio=_sharpe_ratio(trade_pnls_before_tax),
        expectancy=_ratio(total_net, Decimal(closed_count)),
    )


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal:
    if denominator == 0:
        return Decimal("0")
    return numerator / denominator


def _max_drawdown(pnls: list[Decimal]) -> Decimal:
    equity = Decimal("0")
    peak = Decimal("0")
    max_drawdown = Decimal("0")
    for pnl in pnls:
        equity += pnl
        if equity > peak:
            peak = equity
        drawdown = peak - equity
        if drawdown > max_drawdown:
            max_drawdown = drawdown
    return max_drawdown


def _sharpe_ratio(pnls: list[Decimal]) -> Decimal | None:
    count = len(pnls)
    if count < 2:
        return None
    mean = sum(pnls, Decimal("0")) / Decimal(count)
    variance = sum((pnl - mean) ** 2 for pnl in pnls) / Decimal(count - 1)
    if variance == 0:
        return None
    sharpe = (float(mean) / sqrt(float(variance))) * sqrt(count)
    return Decimal(str(sharpe))
