from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True, slots=True)
class ScannerGateThresholds:
    max_risk_penalty: Decimal | None = None
    max_volume_surge: Decimal | None = None
    max_momentum: Decimal | None = None

    @property
    def enabled(self) -> bool:
        return (
            self.max_risk_penalty is not None
            or self.max_volume_surge is not None
            or self.max_momentum is not None
        )


def scanner_gate_reject_reason(
    reasons: dict[str, Any] | None,
    thresholds: ScannerGateThresholds,
    *,
    reason_prefix: str = "scanner_gate_",
) -> str | None:
    if reasons is None:
        return f"{reason_prefix}missing_watchlist"

    risk_penalty = _reason_decimal(reasons.get("risk_penalty"), default=Decimal("0"))
    if thresholds.max_risk_penalty is not None and risk_penalty > thresholds.max_risk_penalty:
        return f"{reason_prefix}risk_penalty"

    volume_surge = _reason_decimal(reasons.get("volume_surge"), default=Decimal("1"))
    if thresholds.max_volume_surge is not None and volume_surge > thresholds.max_volume_surge:
        return f"{reason_prefix}volume_surge"

    momentum = _reason_decimal(reasons.get("momentum"), default=Decimal("0"))
    if thresholds.max_momentum is not None and momentum > thresholds.max_momentum:
        return f"{reason_prefix}momentum"

    return None


def _reason_decimal(value: object, *, default: Decimal) -> Decimal:
    if value is None:
        return default
    return Decimal(str(value))
