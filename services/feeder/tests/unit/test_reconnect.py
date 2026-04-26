"""``streaming.reconnect.BackoffPolicy`` の純関数テスト。"""

from __future__ import annotations

from feeder.streaming.reconnect import BackoffPolicy


def test_base_for_first_attempt_returns_initial() -> None:
    policy = BackoffPolicy(initial_seconds=1.0, max_seconds=60.0, multiplier=2.0)
    assert policy.base_for(1) == 1.0


def test_base_for_doubles_per_attempt() -> None:
    policy = BackoffPolicy(initial_seconds=1.0, max_seconds=60.0, multiplier=2.0)
    assert policy.base_for(2) == 2.0
    assert policy.base_for(3) == 4.0
    assert policy.base_for(4) == 8.0


def test_base_for_clips_at_max() -> None:
    policy = BackoffPolicy(initial_seconds=1.0, max_seconds=10.0, multiplier=2.0)
    assert policy.base_for(10) == 10.0


def test_compute_without_jitter_returns_base() -> None:
    policy = BackoffPolicy(initial_seconds=2.0, max_seconds=60.0, multiplier=2.0, jitter_ratio=0.0)
    # rng が呼ばれないことも暗黙に確認
    assert policy.compute(1, rng=lambda: 0.5) == 2.0
    assert policy.compute(3, rng=lambda: 0.5) == 8.0


def test_compute_with_jitter_uses_provided_rng() -> None:
    policy = BackoffPolicy(
        initial_seconds=10.0,
        max_seconds=60.0,
        multiplier=2.0,
        jitter_ratio=0.2,
    )
    # rng=0.5 → offset = 0 → base そのまま
    assert policy.compute(1, rng=lambda: 0.5) == 10.0
    # rng=0.0 → offset = -2.0
    assert policy.compute(1, rng=lambda: 0.0) == 8.0
    # rng が 1 直前 → offset ≒ +2.0 (rng=1.0 は半開区間外だが許容)
    assert policy.compute(1, rng=lambda: 1.0) == 12.0


def test_compute_clamps_to_zero_minimum() -> None:
    policy = BackoffPolicy(
        initial_seconds=1.0,
        max_seconds=60.0,
        multiplier=2.0,
        jitter_ratio=2.0,  # わざと大きい jitter
    )
    # rng=0.0 → offset = -2.0 → base+offset = -1.0 → 0 にクランプ
    assert policy.compute(1, rng=lambda: 0.0) == 0.0


def test_compute_clamps_within_max_via_base() -> None:
    policy = BackoffPolicy(
        initial_seconds=1.0,
        max_seconds=5.0,
        multiplier=2.0,
        jitter_ratio=0.0,
    )
    # attempt が大きくなっても base が max でクリップされている
    assert policy.compute(10) == 5.0


def test_compute_handles_zero_or_negative_attempt() -> None:
    policy = BackoffPolicy(initial_seconds=3.0, max_seconds=60.0, multiplier=2.0, jitter_ratio=0.0)
    assert policy.compute(0) == 3.0
    assert policy.compute(-1) == 3.0
