from __future__ import annotations

from strategy_ai.parser import parse_response
from trade_contracts.enums import Action


def test_parse_response_plain_json() -> None:
    text = '{"action": "BUY", "confidence": 0.8, "reasoning": "上昇トレンド"}'
    decision = parse_response(text)
    assert decision is not None
    assert decision.action is Action.BUY
    assert decision.confidence == 0.8
    assert decision.reasoning == "上昇トレンド"


def test_parse_response_with_code_fence() -> None:
    text = '```json\n{"action": "SELL", "confidence": 0.7, "reasoning": "x"}\n```'
    decision = parse_response(text)
    assert decision is not None
    assert decision.action is Action.SELL


def test_parse_response_with_surrounding_prose() -> None:
    text = 'The decision is: {"action":"HOLD","confidence":0.4,"reasoning":"flat"} done.'
    decision = parse_response(text)
    assert decision is not None
    assert decision.action is Action.HOLD


def test_parse_response_clamps_confidence() -> None:
    decision = parse_response('{"action": "BUY", "confidence": 1.5, "reasoning": ""}')
    assert decision is not None
    assert decision.confidence == 1.0
    decision = parse_response('{"action": "BUY", "confidence": -0.2, "reasoning": ""}')
    assert decision is not None
    assert decision.confidence == 0.0


def test_parse_response_lowercase_action_normalised() -> None:
    decision = parse_response('{"action": "buy", "confidence": 0.5}')
    assert decision is not None
    assert decision.action is Action.BUY
    assert decision.reasoning == ""


def test_parse_response_rejects_unknown_action() -> None:
    assert parse_response('{"action": "MOON", "confidence": 0.5}') is None


def test_parse_response_rejects_non_numeric_confidence() -> None:
    assert parse_response('{"action": "BUY", "confidence": "high"}') is None
    assert parse_response('{"action": "BUY", "confidence": true}') is None


def test_parse_response_returns_none_on_garbage() -> None:
    assert parse_response("totally not json") is None
    assert parse_response("[1, 2, 3]") is None
