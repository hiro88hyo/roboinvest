"""trade_contracts の公開 API が一通り import でき、基本的な構築が通ることを確認する。"""

from datetime import UTC, datetime
from decimal import Decimal

from trade_contracts import (
    Action,
    KillSwitchState,
    OrderBookSnapshot,
    OrderRequest,
    OrderResult,
    OrderStatus,
    OrderType,
    PositionSide,
    PriceLevel,
    ProcessedFeatures,
    RiskCheck,
    Side,
    SignalSource,
    StrategySignal,
    TickData,
    TradeMode,
    TradeType,
    TradingStyle,
    UnifiedTradeSignal,
)


def test_enums_present() -> None:
    assert Side.BUY.value == "BUY"
    assert PositionSide.LONG.value == "LONG"
    assert Action.HOLD.value == "HOLD"
    assert SignalSource.CONSENSUS.value == "CONSENSUS"
    assert OrderStatus.PENDING.value == "PENDING"
    assert OrderType.MARKET.value == "MARKET"
    assert TradeMode.PAPER.value == "paper"
    assert TradeType.LIVE.value == "live"
    assert TradingStyle.SWING.value == "swing"


def test_tick_data_roundtrip() -> None:
    tick = TickData(symbol="7203", timestamp=datetime.now(UTC), price=Decimal("2500"), volume=100)
    assert tick.symbol == "7203"
    assert tick.volume == 100


def test_unified_trade_signal_minimal() -> None:
    signal = UnifiedTradeSignal(
        symbol="7203",
        action=Action.BUY,
        confidence=0.75,
        signal_source=SignalSource.CONSENSUS,
        holding_type=TradingStyle.DAY,
        stop_loss_price=Decimal("2450"),
        created_at=datetime.now(UTC),
    )
    assert signal.stop_loss_price == Decimal("2450")
    assert signal.target_price is None


def test_models_have_expected_attributes() -> None:
    # スモーク: クラスが import できているだけでなく、Pydantic モデルとして正しく機能する
    assert PriceLevel(price=Decimal("100"), quantity=500).quantity == 500
    assert ProcessedFeatures.model_fields["order_book"].annotation is not None
    assert OrderRequest.model_fields["trade_mode"].annotation is TradeMode
    assert OrderResult.model_fields["status"].annotation is OrderStatus
    assert OrderBookSnapshot.model_fields["bids"].annotation is not None
    assert StrategySignal.model_fields["source"].annotation is SignalSource
    assert RiskCheck(passed=True).passed is True
    assert KillSwitchState.model_fields["daily_pnl"].annotation is Decimal
