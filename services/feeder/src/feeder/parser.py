"""kabuステーション PUSH JSON を contracts のレコードに変換する純関数。

WS で届く 1 メッセージ = 1 銘柄の状態通知だが、メッセージには「直近約定」と
「板スナップショット」の両方が含まれることがある。``parse_push_message`` は
含まれているフィールドの有無で ``TickData`` / ``OrderBookSnapshot`` を 0..2 件
派生させて返す。

設計メモ:
- 価格は受信時に必ず ``Decimal(str(value))`` で文字列経由で変換する (float 経由
  は丸め誤差を生むため禁止)
- ``CurrentPriceTime`` は ISO 8601 文字列で来る前提。タイムゾーン情報を含まない
  場合は JST として解釈する
- 板の各レベル ``Price`` または ``Qty`` が ``None`` / ``0`` (特別気配など) の
  ものはスキップする
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from trade_contracts.market import OrderBookSnapshot, PriceLevel, TickData

JST = timezone(timedelta(hours=9), name="JST")


def parse_push_message(payload: dict[str, Any]) -> list[TickData | OrderBookSnapshot]:
    """1 件の PUSH JSON から派生する contracts レコードを返す。

    判別ルール:
    - ``CurrentPrice`` と ``CurrentPriceTime`` が両方揃っていれば ``TickData``
    - ``Bid1``/``Ask1`` のいずれかに ``Price`` があれば ``OrderBookSnapshot``
    - どちらにも該当しなければ空リスト (無視)
    """
    symbol = _extract_symbol(payload)
    if symbol is None:
        return []

    results: list[TickData | OrderBookSnapshot] = []

    tick = _try_build_tick(symbol, payload)
    if tick is not None:
        results.append(tick)

    book = _try_build_book(symbol, payload)
    if book is not None:
        results.append(book)

    return results


def _extract_symbol(payload: dict[str, Any]) -> str | None:
    sym = payload.get("Symbol")
    if isinstance(sym, str) and sym:
        return sym
    if isinstance(sym, int):
        return str(sym)
    return None


def _try_build_tick(symbol: str, payload: dict[str, Any]) -> TickData | None:
    price_raw = payload.get("CurrentPrice")
    ts_raw = payload.get("CurrentPriceTime")
    if price_raw is None or ts_raw is None:
        return None

    price = _to_decimal(price_raw)
    if price is None:
        return None

    ts = _parse_timestamp(ts_raw)
    if ts is None:
        return None

    volume_raw = payload.get("TradingVolume")
    volume = _to_int(volume_raw, default=0)
    if volume < 0:
        volume = 0

    return TickData(symbol=symbol, timestamp=ts, price=price, volume=volume)


def _try_build_book(symbol: str, payload: dict[str, Any]) -> OrderBookSnapshot | None:
    bids = _collect_levels(payload, prefix="Bid")
    asks = _collect_levels(payload, prefix="Ask")

    if not bids and not asks:
        return None

    ts = _parse_timestamp(payload.get("CurrentPriceTime"))
    if ts is None:
        # 板更新のタイムスタンプは、kabu PUSH では明示的なフィールドが無いため
        # ``CurrentPriceTime`` を流用する。それも無ければ板は無視する。
        return None

    return OrderBookSnapshot(
        symbol=symbol,
        timestamp=ts,
        bids=bids,
        asks=asks,
    )


def _collect_levels(payload: dict[str, Any], *, prefix: str) -> list[PriceLevel]:
    levels: list[PriceLevel] = []
    for i in range(1, 11):
        raw = payload.get(f"{prefix}{i}")
        if not isinstance(raw, dict):
            continue
        price = _to_decimal(raw.get("Price"))
        qty = _to_int(raw.get("Qty"), default=0)
        if price is None or qty <= 0:
            continue
        levels.append(PriceLevel(price=price, quantity=qty))
    return levels


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        # bool は int の派生だが値として意味を持たないので除外
        return None
    if isinstance(value, int | float | str):
        try:
            return Decimal(str(value))
        except (ValueError, ArithmeticError):
            return None
    return None


def _to_int(value: Any, *, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            try:
                return int(float(value))
            except ValueError:
                return default
    return default


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=JST)
    if not isinstance(value, str) or not value:
        return None
    try:
        # kabu PUSH は "2026-04-25T09:00:00.123456+09:00" のような ISO 文字列
        # で来ることが多いが、TZ が抜けるケースもあるため fromisoformat に任せ
        # た上で naive ならば JST と解釈する。
        ts = datetime.fromisoformat(value)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=JST)
    return ts
