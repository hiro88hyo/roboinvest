from __future__ import annotations

from decimal import Decimal

from trade_contracts.enums import Action


def buy_exit_fields(
    *,
    action: Action,
    price: Decimal,
    target_pct: Decimal | None,
    trailing_stop_pct: Decimal | None,
) -> dict[str, Decimal | None]:
    if action is not Action.BUY:
        return {
            "target_price": None,
            "trailing_stop_pct": None,
        }

    target_price = None
    if target_pct is not None and target_pct > 0:
        target_price = price * (Decimal("1") + target_pct)

    trailing = (
        trailing_stop_pct if trailing_stop_pct is not None and trailing_stop_pct > 0 else None
    )
    return {
        "target_price": target_price,
        "trailing_stop_pct": trailing,
    }
