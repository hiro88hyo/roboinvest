from __future__ import annotations

from decimal import Decimal

from gateway.config import GatewaySettings, RiskConfig


def test_settings_defaults() -> None:
    s = GatewaySettings(_env_file=None)
    assert s.log_level == "INFO"
    assert s.capital == Decimal("1000000")
    assert s.max_risk_per_trade_pct == Decimal("0.02")
    assert s.swing_risk_scale == Decimal("0.5")
    assert s.default_stop_loss_spread_pct == Decimal("0.02")
    assert s.min_lot_size == 100
    assert s.pubsub_subscription_trade_signals == "gateway-trade-signals"
    assert s.pubsub_topic_live_orders == "live-orders"
    assert s.pubsub_topic_paper_orders == "paper-orders"
    assert s.oms_live_max_qty_per_order is None
    assert s.live_signal_max_age_seconds == 300.0
    assert s.live_day_new_buy_start_time == "09:05"
    assert s.live_day_new_buy_cutoff_time == "14:30"


def test_settings_env_override(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("CAPITAL", "500000")
    monkeypatch.setenv("MAX_RISK_PER_TRADE_PCT", "0.01")
    monkeypatch.setenv("MIN_LOT_SIZE", "200")
    monkeypatch.setenv("OMS_LIVE_MAX_QTY_PER_ORDER", "100")
    monkeypatch.setenv("LIVE_SIGNAL_MAX_AGE_SECONDS", "120")
    monkeypatch.setenv("LIVE_DAY_NEW_BUY_START_TIME", "09:15")
    s = GatewaySettings(_env_file=None)
    assert s.capital == Decimal("500000")
    assert s.max_risk_per_trade_pct == Decimal("0.01")
    assert s.min_lot_size == 200
    assert s.oms_live_max_qty_per_order == 100
    assert s.live_signal_max_age_seconds == 120.0
    assert s.live_day_new_buy_start_time == "09:15"


def test_risk_config_from_settings() -> None:
    s = GatewaySettings(_env_file=None)
    cfg = RiskConfig.from_settings(s)
    assert cfg.capital == s.capital
    assert cfg.max_risk_per_trade_pct == s.max_risk_per_trade_pct
    assert cfg.swing_risk_scale == s.swing_risk_scale
    assert cfg.default_stop_loss_spread_pct == s.default_stop_loss_spread_pct
    assert cfg.min_lot_size == s.min_lot_size
