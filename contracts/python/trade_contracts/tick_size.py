from __future__ import annotations

from decimal import Decimal

_STANDARD_PRICE_BANDS: tuple[tuple[Decimal | None, Decimal], ...] = (
    (Decimal("3000"), Decimal("1")),
    (Decimal("5000"), Decimal("5")),
    (Decimal("30000"), Decimal("10")),
    (Decimal("50000"), Decimal("50")),
    (Decimal("300000"), Decimal("100")),
    (Decimal("500000"), Decimal("500")),
    (Decimal("3000000"), Decimal("1000")),
    (Decimal("5000000"), Decimal("5000")),
    (Decimal("30000000"), Decimal("10000")),
    (Decimal("50000000"), Decimal("50000")),
    (None, Decimal("100000")),
)

_TOPIX500_PRICE_BANDS: tuple[tuple[Decimal | None, Decimal], ...] = (
    (Decimal("1000"), Decimal("0.1")),
    (Decimal("3000"), Decimal("0.5")),
    (Decimal("5000"), Decimal("1")),
    (Decimal("30000"), Decimal("5")),
    (Decimal("50000"), Decimal("10")),
    (Decimal("300000"), Decimal("50")),
    (Decimal("500000"), Decimal("100")),
    (Decimal("3000000"), Decimal("500")),
    (Decimal("5000000"), Decimal("1000")),
    (Decimal("30000000"), Decimal("5000")),
    (Decimal("50000000"), Decimal("10000")),
    (None, Decimal("50000")),
)


def tse_tick_size(price: Decimal, *, is_topix500: bool = False) -> Decimal:
    """Return the TSE domestic stock tick size for a price band.

    ``is_topix500`` selects the finer TOPIX500 table. Callers should keep it false
    unless they have a trusted symbol membership source.
    """
    if price <= 0:
        raise ValueError(f"price must be positive, got {price}")

    bands = _TOPIX500_PRICE_BANDS if is_topix500 else _STANDARD_PRICE_BANDS
    for upper_bound, tick_size in bands:
        if upper_bound is None or price <= upper_bound:
            return tick_size
    raise AssertionError("unreachable tick size band")
