from __future__ import annotations

from collections.abc import Callable

from .base import Strategy
from .config import StrategyRuleSettings

StrategyFactory = Callable[[StrategyRuleSettings], Strategy]

_REGISTRY: dict[str, StrategyFactory] = {}


def register(name: str, factory: StrategyFactory) -> None:
    """Register a strategy factory under `name`. Last write wins."""
    _REGISTRY[name] = factory


def unregister(name: str) -> None:
    _REGISTRY.pop(name, None)


def registered_names() -> list[str]:
    return sorted(_REGISTRY)


def build_strategies(settings: StrategyRuleSettings) -> list[Strategy]:
    """Instantiate the strategies enabled in `settings` in declaration order.

    Unknown names are silently skipped so operators can toggle strategies
    via env without breaking startup. Use `registered_names()` to discover
    what is available.
    """
    out: list[Strategy] = []
    for name in settings.strategies_enabled:
        factory = _REGISTRY.get(name)
        if factory is None:
            continue
        out.append(factory(settings))
    return out
