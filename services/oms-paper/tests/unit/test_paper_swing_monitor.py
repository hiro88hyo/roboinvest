from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from oms_paper._testing import make_paper_position
from oms_paper.swing_monitor import evaluate_swing_exit, find_max_hold_due_swing_positions
from trade_contracts.enums import TradingStyle


def test_day_position_is_always_hold() -> None:
    pos = make_paper_position(
        holding_type=TradingStyle.DAY,
        stop_loss_price=Decimal("950"),
        target_price=Decimal("1100"),
        max_hold_days=5,
        trailing_stop_pct=Decimal("0.02"),
    )
    decision = evaluate_swing_exit(
        position=pos,
        latest_price=Decimal("900"),  # 損切り条件は "成立" するが day なので無視
        now=datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
    )
    assert decision.action == "hold"
    assert decision.reason is None
    assert decision.new_stop_loss_price is None


def test_swing_with_no_thresholds_is_hold() -> None:
    pos = make_paper_position(
        holding_type=TradingStyle.SWING,
        stop_loss_price=None,
        target_price=None,
        max_hold_days=None,
        trailing_stop_pct=None,
    )
    decision = evaluate_swing_exit(
        position=pos,
        latest_price=Decimal("1000"),
        now=datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
    )
    assert decision.action == "hold"


def test_stop_loss_breach_triggers_exit() -> None:
    pos = make_paper_position(
        holding_type=TradingStyle.SWING,
        stop_loss_price=Decimal("950"),
        target_price=Decimal("1100"),
        max_hold_days=5,
        trailing_stop_pct=Decimal("0.02"),
    )
    decision = evaluate_swing_exit(
        position=pos,
        latest_price=Decimal("950"),  # 等しい場合も損切り
        now=datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
    )
    assert decision.action == "exit"
    assert decision.reason == "stop_loss"
    assert decision.new_stop_loss_price is None


def test_stop_loss_takes_priority_over_target() -> None:
    """損切りと利確が同時に成立しているような (現実には起きない) 矛盾入力では
    安全側 = stop_loss を採用する。"""
    pos = make_paper_position(
        holding_type=TradingStyle.SWING,
        stop_loss_price=Decimal("1100"),
        target_price=Decimal("900"),
    )
    decision = evaluate_swing_exit(
        position=pos,
        latest_price=Decimal("1000"),
        now=datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
    )
    assert decision.action == "exit"
    assert decision.reason == "stop_loss"


def test_target_price_hit_triggers_exit() -> None:
    pos = make_paper_position(
        holding_type=TradingStyle.SWING,
        stop_loss_price=Decimal("950"),
        target_price=Decimal("1100"),
    )
    decision = evaluate_swing_exit(
        position=pos,
        latest_price=Decimal("1100"),  # 等しい場合も利確
        now=datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
    )
    assert decision.action == "exit"
    assert decision.reason == "target"


def test_max_hold_days_exit() -> None:
    opened = datetime(2026, 4, 25, 9, 0, tzinfo=UTC)
    pos = make_paper_position(
        holding_type=TradingStyle.SWING,
        max_hold_days=5,
        opened_at=opened,
    )
    # 5 日後 (== max_hold_days) で経過扱い
    decision = evaluate_swing_exit(
        position=pos,
        latest_price=Decimal("1000"),
        now=opened + timedelta(days=5),
    )
    assert decision.action == "exit"
    assert decision.reason == "max_hold_days"


def test_max_hold_days_not_yet_expired_holds() -> None:
    """``.date()`` 同士の差分なので、暦日が境界を越えるかどうかで判定される。"""
    opened = datetime(2026, 4, 25, 9, 0, tzinfo=UTC)
    pos = make_paper_position(
        holding_type=TradingStyle.SWING,
        max_hold_days=5,
        opened_at=opened,
    )
    # 4 暦日経過 (4-25 → 4-29) は max_hold_days=5 未満
    decision = evaluate_swing_exit(
        position=pos,
        latest_price=Decimal("1000"),
        now=opened + timedelta(days=4),
    )
    assert decision.action == "hold"


def test_find_max_hold_due_swing_positions_only_returns_due_swing_positions() -> None:
    opened = datetime(2026, 4, 25, 9, 0, tzinfo=UTC)
    due = make_paper_position(
        symbol="7203",
        holding_type=TradingStyle.SWING,
        max_hold_days=5,
        opened_at=opened,
    )
    not_due = make_paper_position(
        symbol="6758",
        holding_type=TradingStyle.SWING,
        max_hold_days=6,
        opened_at=opened,
    )
    no_max_hold = make_paper_position(
        symbol="9984",
        holding_type=TradingStyle.SWING,
        max_hold_days=None,
        opened_at=opened,
    )
    due_day_position = make_paper_position(
        symbol="8306",
        holding_type=TradingStyle.DAY,
        max_hold_days=5,
        opened_at=opened,
    )

    due_positions = find_max_hold_due_swing_positions(
        positions=[not_due, due, no_max_hold, due_day_position],
        now=opened + timedelta(days=5),
    )

    assert [position.symbol for position in due_positions] == ["7203"]


def test_max_hold_days_only_priority_after_stop_and_target() -> None:
    """stop_loss / target 未設定時、max_hold_days が判定される。"""
    opened = datetime(2026, 4, 25, 9, 0, tzinfo=UTC)
    pos = make_paper_position(
        holding_type=TradingStyle.SWING,
        max_hold_days=3,
        opened_at=opened,
        stop_loss_price=Decimal("900"),
        target_price=Decimal("1100"),
    )
    # stop も target も触れない価格、max_hold_days 経過
    decision = evaluate_swing_exit(
        position=pos,
        latest_price=Decimal("1000"),
        now=opened + timedelta(days=3),
    )
    assert decision.action == "exit"
    assert decision.reason == "max_hold_days"


def test_trailing_creates_new_stop_when_none_exists() -> None:
    pos = make_paper_position(
        holding_type=TradingStyle.SWING,
        stop_loss_price=None,
        trailing_stop_pct=Decimal("0.02"),  # 2%
    )
    decision = evaluate_swing_exit(
        position=pos,
        latest_price=Decimal("1000"),
        now=datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
    )
    assert decision.action == "trail"
    assert decision.new_stop_loss_price == Decimal("980")  # 1000 * 0.98


def test_trailing_lifts_stop_when_price_rises() -> None:
    pos = make_paper_position(
        holding_type=TradingStyle.SWING,
        stop_loss_price=Decimal("980"),
        trailing_stop_pct=Decimal("0.02"),
    )
    decision = evaluate_swing_exit(
        position=pos,
        latest_price=Decimal("1100"),  # candidate = 1078 > 980
        now=datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
    )
    assert decision.action == "trail"
    assert decision.new_stop_loss_price == Decimal("1078")


def test_trailing_does_not_move_stop_backward() -> None:
    """高値からの押し戻しでは stop は単調増加のみ (hold)。

    シナリオ: 一度 1100 まで上がって stop が 1078 まで切り上げられた状態で、
    今の price が 1090 (stop より上、前回高値より下)。candidate = 1068 < 1078
    なので stop は更新しない。
    """
    pos = make_paper_position(
        holding_type=TradingStyle.SWING,
        stop_loss_price=Decimal("1078"),
        trailing_stop_pct=Decimal("0.02"),
    )
    decision = evaluate_swing_exit(
        position=pos,
        latest_price=Decimal(
            "1090"
        ),  # > stop なので exit はしない、candidate < stop なので trail もしない
        now=datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
    )
    assert decision.action == "hold"
    assert decision.new_stop_loss_price is None


def test_trailing_equal_candidate_holds() -> None:
    """新候補が既存 stop と同値なら更新不要。"""
    pos = make_paper_position(
        holding_type=TradingStyle.SWING,
        stop_loss_price=Decimal("980"),
        trailing_stop_pct=Decimal("0.02"),
    )
    decision = evaluate_swing_exit(
        position=pos,
        latest_price=Decimal("1000"),  # candidate = 980 == 980
        now=datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
    )
    assert decision.action == "hold"


def test_trailing_rounding_half_up() -> None:
    """ROUND_HALF_UP で 1 円単位丸め。"""
    pos = make_paper_position(
        holding_type=TradingStyle.SWING,
        stop_loss_price=None,
        trailing_stop_pct=Decimal("0.025"),  # 2.5%
    )
    # 1234 * 0.975 = 1203.15 → 1203
    decision = evaluate_swing_exit(
        position=pos,
        latest_price=Decimal("1234"),
        now=datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
    )
    assert decision.action == "trail"
    assert decision.new_stop_loss_price == Decimal("1203")

    # 1235 * 0.975 = 1204.125 → 1204
    pos2 = make_paper_position(
        holding_type=TradingStyle.SWING,
        stop_loss_price=None,
        trailing_stop_pct=Decimal("0.025"),
    )
    decision2 = evaluate_swing_exit(
        position=pos2,
        latest_price=Decimal("1235"),
        now=datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
    )
    assert decision2.new_stop_loss_price == Decimal("1204")


def test_exit_takes_priority_over_trail() -> None:
    """損切り条件と trail 候補が両方成立する状況。stop_loss 側が優先される。"""
    pos = make_paper_position(
        holding_type=TradingStyle.SWING,
        stop_loss_price=Decimal("980"),
        trailing_stop_pct=Decimal("0.02"),
    )
    # stop_loss 980 を下回るが、trail 候補は計算上 980 → exit が勝つ
    decision = evaluate_swing_exit(
        position=pos,
        latest_price=Decimal("970"),
        now=datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
    )
    assert decision.action == "exit"
    assert decision.reason == "stop_loss"
