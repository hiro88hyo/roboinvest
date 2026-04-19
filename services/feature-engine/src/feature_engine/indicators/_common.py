from __future__ import annotations

import polars as pl

SYMBOL_COL = "symbol"


def maybe_group(expr: pl.Expr, df: pl.DataFrame) -> pl.Expr:
    """銘柄列があれば銘柄ごとに独立して評価する。"""
    if SYMBOL_COL in df.columns:
        return expr.over(SYMBOL_COL)
    return expr


def require_columns(df: pl.DataFrame, *columns: str) -> None:
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise ValueError(f"missing required columns: {missing}")


def require_positive_int(name: str, value: int) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer, got {value}")
