"""__main__ CLI (oms-live) の単体テスト。"""

from __future__ import annotations

import pytest
from oms_live.__main__ import main


def test_cli_stream_requires_pubsub_project(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PUBSUB_PROJECT_ID", "")
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "")
    monkeypatch.setenv("KABU_API_PASSWORD", "")
    monkeypatch.setenv("KABU_ORDER_PASSWORD", "")
    rc = main(["stream", "--iterations", "0"])
    assert rc == 2


def test_cli_stream_requires_supabase(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PUBSUB_PROJECT_ID", "trade-ai-dev")
    monkeypatch.setenv("PUBSUB_EMULATOR_HOST", "pubsub:8085")
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "")
    rc = main(["stream", "--iterations", "0"])
    assert rc == 2


def test_cli_stream_requires_kabu_passwords(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PUBSUB_PROJECT_ID", "trade-ai-dev")
    monkeypatch.setenv("PUBSUB_EMULATOR_HOST", "pubsub:8085")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "k")
    monkeypatch.setenv("KABU_API_PASSWORD", "")
    monkeypatch.setenv("KABU_ORDER_PASSWORD", "")
    rc = main(["stream", "--iterations", "0"])
    assert rc == 2


def test_cli_stream_iterations_zero_with_env_runs_no_op(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """env が揃っていて --iterations 0 のときは何もせず正常終了する。

    Pub/Sub / Supabase / kabu クライアントは ``__aenter__`` 時にネットワーク接続
    しないため、iterations=0 ならハンドシェイクは発生しない。
    """
    monkeypatch.setenv("PUBSUB_PROJECT_ID", "trade-ai-dev")
    monkeypatch.setenv("PUBSUB_EMULATOR_HOST", "pubsub:8085")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "k")
    monkeypatch.setenv("KABU_API_BASE_URL", "http://localhost:18081/kabusapi")
    monkeypatch.setenv("KABU_API_PASSWORD", "api-pw")
    monkeypatch.setenv("KABU_ORDER_PASSWORD", "order-pw")
    rc = main(["stream", "--iterations", "0", "--no-closeout"])
    assert rc == 0


def test_cli_unknown_command(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SystemExit):
        main(["bogus"])
