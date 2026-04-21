from __future__ import annotations

import pytest
from aggregator.__main__ import main


def test_main_without_args_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "aggregator" in out.lower()


def test_main_backtest_not_implemented(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["backtest"]) == 2
    err = capsys.readouterr().err
    assert "not implemented" in err


def test_main_stream_not_implemented(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["stream"]) == 2
    err = capsys.readouterr().err
    assert "not implemented" in err


def test_main_unknown_mode_rejected(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["bogus"])
