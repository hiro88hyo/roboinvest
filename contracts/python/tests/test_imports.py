"""trade_contracts の公開 API が一通り import でき、基本的な構築が通ることを確認する。"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError
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
    RoutingIntent,
    ScannerGateThresholds,
    Side,
    SignalSource,
    StrategySignal,
    TickData,
    TradeMode,
    TradeType,
    TradingStyle,
    UnifiedTradeSignal,
    scanner_gate_reject_reason,
    tse_tick_size,
)


def test_enums_present() -> None:
    assert Side.BUY.value == "BUY"
    assert PositionSide.LONG.value == "LONG"
    assert Action.HOLD.value == "HOLD"
    assert SignalSource.CONSENSUS.value == "CONSENSUS"
    assert OrderStatus.PENDING.value == "PENDING"
    assert OrderType.MARKET.value == "MARKET"
    assert TradeMode.PAPER.value == "paper"
    assert RoutingIntent.PAPER_ONLY.value == "PAPER_ONLY"
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
    assert ProcessedFeatures.model_fields["spread_bps"].annotation == Decimal | None
    assert ProcessedFeatures.model_fields["tick_size"].annotation == Decimal | None
    assert ProcessedFeatures.model_fields["session_phase"].annotation == str | None
    assert StrategySignal.model_fields["spread_bps"].annotation == Decimal | None
    assert StrategySignal.model_fields["target_price"].annotation == Decimal | None
    assert StrategySignal.model_fields["stop_loss_pct"].annotation == Decimal | None
    assert StrategySignal.model_fields["trailing_stop_pct"].annotation == Decimal | None
    assert StrategySignal.model_fields["holding_type"].annotation == TradingStyle | None
    assert UnifiedTradeSignal.model_fields["tick_size"].annotation == Decimal | None
    assert UnifiedTradeSignal.model_fields["ask_depth_5"].annotation == int | None
    assert OrderRequest.model_fields["trade_mode"].annotation is TradeMode
    assert OrderRequest.model_fields["holding_type"].annotation == TradingStyle | None
    assert OrderRequest.model_fields["stop_loss_price"].annotation == Decimal | None
    assert OrderRequest.model_fields["stop_loss_pct"].annotation == Decimal | None
    assert OrderResult.model_fields["status"].annotation is OrderStatus
    assert OrderBookSnapshot.model_fields["bids"].annotation is not None
    assert OrderBookSnapshot.model_fields["received_at"].annotation == datetime | None
    assert StrategySignal.model_fields["source"].annotation is SignalSource
    assert RiskCheck(passed=True).passed is True
    assert KillSwitchState.model_fields["daily_pnl"].annotation is Decimal


def test_relative_stop_intent_roundtrip() -> None:
    stamp = datetime.now(UTC)
    signal = StrategySignal(
        source=SignalSource.RULE,
        symbol="7203",
        action=Action.BUY,
        confidence=0.8,
        holding_type=TradingStyle.SWING,
        stop_loss_pct=Decimal("0.10"),
        max_hold_days=20,
        created_at=stamp,
    )
    roundtrip = StrategySignal.model_validate_json(signal.model_dump_json())
    assert roundtrip.stop_loss_pct == Decimal("0.10")
    assert roundtrip.stop_loss_price is None


def test_event_identity_makes_signal_chain_ids_deterministic() -> None:
    stamp = datetime(2026, 7, 10, 0, 30, tzinfo=UTC)
    strategy_payload = {
        "source": SignalSource.RULE,
        "routing_intent": RoutingIntent.PAPER_ONLY,
        "strategy_key": "event-cluster-v1",
        "candidate_id": "cluster:7203:2026-07-10",
        "symbol": "7203",
        "action": Action.BUY,
        "confidence": 0.8,
        "created_at": stamp,
    }
    first = StrategySignal.model_validate(strategy_payload)
    redelivered = StrategySignal.model_validate(strategy_payload)
    assert first.signal_id == redelivered.signal_id

    unified_payload = {
        "symbol": "7203",
        "action": Action.BUY,
        "confidence": 0.8,
        "signal_source": SignalSource.RULE,
        "strategy_signal_id_a": first.signal_id,
        "routing_intent": RoutingIntent.PAPER_ONLY,
        "strategy_key": first.strategy_key,
        "candidate_id": first.candidate_id,
        "holding_type": TradingStyle.SWING,
        "created_at": stamp,
    }
    unified = UnifiedTradeSignal.model_validate(unified_payload)
    unified_redelivery = UnifiedTradeSignal.model_validate(unified_payload)
    assert unified.signal_id == unified_redelivery.signal_id

    order_payload = {
        "unified_signal_id": unified.signal_id,
        "symbol": "7203",
        "side": Side.BUY,
        "quantity": 100,
        "trade_mode": TradeMode.PAPER,
        "signal_source": SignalSource.RULE,
        "routing_intent": RoutingIntent.PAPER_ONLY,
        "strategy_key": first.strategy_key,
        "candidate_id": first.candidate_id,
        "created_at": stamp,
    }
    order = OrderRequest.model_validate(order_payload)
    order_redelivery = OrderRequest.model_validate(order_payload)
    assert order.order_id == order_redelivery.order_id


def test_strategy_identity_is_all_or_nothing() -> None:
    with pytest.raises(ValidationError, match="must be provided together"):
        StrategySignal(
            source=SignalSource.RULE,
            strategy_key="event-cluster-v1",
            symbol="7203",
            action=Action.BUY,
            confidence=0.8,
            created_at=datetime.now(UTC),
        )


def test_paper_only_order_cannot_be_constructed_for_live() -> None:
    with pytest.raises(ValidationError, match="require trade_mode=paper"):
        OrderRequest(
            symbol="7203",
            side=Side.BUY,
            quantity=100,
            trade_mode=TradeMode.LIVE,
            signal_source=SignalSource.RULE,
            routing_intent=RoutingIntent.PAPER_ONLY,
            created_at=datetime.now(UTC),
        )


def test_stop_loss_price_and_pct_are_mutually_exclusive() -> None:
    stamp = datetime.now(UTC)
    with pytest.raises(ValidationError, match="mutually exclusive"):
        StrategySignal(
            source=SignalSource.RULE,
            symbol="7203",
            action=Action.BUY,
            confidence=0.8,
            stop_loss_price=Decimal("900"),
            stop_loss_pct=Decimal("0.10"),
            created_at=stamp,
        )
    with pytest.raises(ValidationError, match="mutually exclusive"):
        UnifiedTradeSignal(
            symbol="7203",
            action=Action.BUY,
            confidence=0.8,
            signal_source=SignalSource.RULE,
            holding_type=TradingStyle.SWING,
            stop_loss_price=Decimal("900"),
            stop_loss_pct=Decimal("0.10"),
            created_at=stamp,
        )
    with pytest.raises(ValidationError, match="mutually exclusive"):
        OrderRequest(
            symbol="7203",
            side=Side.BUY,
            quantity=100,
            trade_mode=TradeMode.PAPER,
            signal_source=SignalSource.RULE,
            stop_loss_price=Decimal("900"),
            stop_loss_pct=Decimal("0.10"),
            created_at=stamp,
        )


@pytest.mark.parametrize("value", [Decimal("0"), Decimal("1"), Decimal("-0.10")])
def test_stop_loss_pct_requires_positive_fraction(value: Decimal) -> None:
    with pytest.raises(ValidationError):
        StrategySignal(
            source=SignalSource.RULE,
            symbol="7203",
            action=Action.BUY,
            confidence=0.8,
            stop_loss_pct=value,
            created_at=datetime.now(UTC),
        )


def test_stop_loss_pct_is_buy_only_and_paper_only_at_order_boundary() -> None:
    stamp = datetime.now(UTC)
    with pytest.raises(ValidationError, match="only valid for BUY signals"):
        StrategySignal(
            source=SignalSource.RULE,
            symbol="7203",
            action=Action.SELL,
            confidence=0.8,
            stop_loss_pct=Decimal("0.10"),
            created_at=stamp,
        )
    with pytest.raises(ValidationError, match="only valid for BUY signals"):
        UnifiedTradeSignal(
            symbol="7203",
            action=Action.HOLD,
            confidence=0.8,
            signal_source=SignalSource.RULE,
            holding_type=TradingStyle.SWING,
            stop_loss_pct=Decimal("0.10"),
            created_at=stamp,
        )
    with pytest.raises(ValidationError, match="only valid for BUY orders"):
        OrderRequest(
            symbol="7203",
            side=Side.SELL,
            quantity=100,
            trade_mode=TradeMode.PAPER,
            signal_source=SignalSource.RULE,
            stop_loss_pct=Decimal("0.10"),
            created_at=stamp,
        )
    with pytest.raises(ValidationError, match="only supported for paper orders"):
        OrderRequest(
            symbol="7203",
            side=Side.BUY,
            quantity=100,
            trade_mode=TradeMode.LIVE,
            signal_source=SignalSource.RULE,
            stop_loss_pct=Decimal("0.10"),
            created_at=stamp,
        )


def test_tse_tick_size_tables() -> None:
    assert tse_tick_size(Decimal("2500")) == Decimal("1")
    assert tse_tick_size(Decimal("4000")) == Decimal("5")
    assert tse_tick_size(Decimal("2500"), is_topix500=True) == Decimal("0.5")


def test_scanner_gate_reject_reason() -> None:
    thresholds = ScannerGateThresholds(
        max_risk_penalty=Decimal("1.5"),
        max_volume_surge=Decimal("2.1"),
        max_momentum=Decimal("0.4"),
    )
    assert scanner_gate_reject_reason(None, thresholds) == "scanner_gate_missing_watchlist"
    assert (
        scanner_gate_reject_reason({"risk_penalty": 2, "volume_surge": 1}, thresholds)
        == "scanner_gate_risk_penalty"
    )
    assert (
        scanner_gate_reject_reason({"risk_penalty": 1, "volume_surge": 3}, thresholds)
        == "scanner_gate_volume_surge"
    )
    assert (
        scanner_gate_reject_reason(
            {"risk_penalty": 1, "volume_surge": 1, "momentum": 0.5}, thresholds
        )
        == "scanner_gate_momentum"
    )
    assert (
        scanner_gate_reject_reason(
            {"risk_penalty": 1, "volume_surge": 1, "momentum": 0.1}, thresholds
        )
        is None
    )
