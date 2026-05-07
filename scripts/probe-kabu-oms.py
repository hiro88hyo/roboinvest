#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "httpx>=0.27",
# ]
# ///
"""kabuステーション API (OMS Live 系) 疎通スクリプト。

``probe-kabu.py`` が Feeder 系 (token / wallet / symbol / WS) を確認するのに対し、
こちらは OMS Live が叩く REST 群の挙動を観測する:

  1. POST /kabusapi/token            (トークン取得)
  2. GET  /kabusapi/wallet/cash      (買付余力)
  3. GET  /kabusapi/symbol/{c}@{e}   (銘柄情報・呼値)
  4. GET  /kabusapi/board/{c}@{e}    (板情報 + 現在値)
  5. GET  /kabusapi/positions        (現物残高)
  6. GET  /kabusapi/orders           (注文一覧, 直近 N 件)

  -- 以下は --send 指定時のみ実行 (デフォルト OFF) --
  7. POST /kabusapi/sendorder        (発注)
  8. GET  /kabusapi/orders?id=<id>   (発注後の状態取得)
  9. PUT  /kabusapi/cancelorder      (--auto-cancel 指定時のみ)

検証環境 (18081) は約定が市場時間内のみだが、トークン発行・残高照会・板取得・
注文受付までは 24h 通る。Golden Week 中など休日に sendorder が「受付」「reject」
「待機」のどれを返すかを観測する目的で ``--send`` を opt-in で持たせている。

使い方:
  KABU_API_PASSWORD=xxx uv run scripts/probe-kabu-oms.py --env test
  KABU_API_PASSWORD=xxx uv run scripts/probe-kabu-oms.py --env test --skip wallet symbol
  KABU_API_PASSWORD=xxx KABU_ORDER_PASSWORD=yyy \\
      uv run scripts/probe-kabu-oms.py --env test --send --order-qty 100 --auto-cancel
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from typing import Any

import httpx

PORT_BY_ENV = {"prod": 18080, "test": 18081}


@dataclass
class Endpoint:
    host: str
    port: int

    @property
    def http_base(self) -> str:
        return f"http://{self.host}:{self.port}/kabusapi"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--env",
        choices=["prod", "test"],
        default="test",
        help="本番(18080) or 検証(18081)",
    )
    p.add_argument("--host", default="localhost", help="kabuステーションが動いているホスト")
    p.add_argument(
        "--port",
        type=int,
        default=None,
        help=(
            "接続先ポート。未指定なら --env から決定 (prod=18080, test=18081)。"
            "Caddy リバプロ経由なら 28080/28081 等を指定する"
        ),
    )
    p.add_argument(
        "--host-header",
        default=None,
        help=(
            "HTTP の Host ヘッダを上書き (既定は --host と同じ)。"
            "LAN 越しに繋ぐと Windows http.sys が Host: localhost しか受け付けず "
            "400 'Invalid Hostname' を返すため、その場合は `--host-header localhost` を指定する。"
        ),
    )
    p.add_argument(
        "--symbol",
        default="7203",
        help="銘柄情報・発注に使う証券コード (default: 7203 トヨタ)",
    )
    p.add_argument(
        "--exchange",
        type=int,
        default=1,
        help="市場コード (1=東証, 3=名証, default: 1)",
    )
    p.add_argument(
        "--skip",
        nargs="*",
        choices=["wallet", "symbol", "board", "positions", "orders"],
        default=[],
        help="GET 系でスキップしたいチェック",
    )
    p.add_argument(
        "--orders-limit",
        type=int,
        default=5,
        help="GET /orders 結果のうち先頭何件を露出するか (default: 5)",
    )

    # --- send 系 (opt-in) ---
    p.add_argument(
        "--send",
        action="store_true",
        help=(
            "POST /sendorder を実行する。デフォルト OFF。"
            "指定時は KABU_ORDER_PASSWORD 必須。本番 (--env prod) では実発注になる点に注意。"
        ),
    )
    p.add_argument(
        "--order-side",
        choices=["buy", "sell"],
        default="buy",
        help="発注サイド (default: buy)",
    )
    p.add_argument(
        "--order-qty",
        type=int,
        default=100,
        help="発注数量 (default: 100)",
    )
    p.add_argument(
        "--order-type",
        choices=["market", "limit"],
        default="market",
        help="注文種別 (default: market)",
    )
    p.add_argument(
        "--order-price",
        type=float,
        default=0.0,
        help="指値価格 (--order-type limit 時のみ使用)",
    )
    p.add_argument(
        "--account-type",
        type=int,
        default=4,
        help="kabu AccountType (4=特定, 2=一般, default: 4)",
    )
    p.add_argument(
        "--auto-cancel",
        action="store_true",
        help="sendorder 成功時に取得した OrderId を即座に PUT /cancelorder で取消す",
    )
    return p.parse_args()


def step(label: str) -> None:
    print(f"\n=== {label} ===", flush=True)


def ok(msg: str) -> None:
    print(f"  OK  {msg}", flush=True)


def fail(msg: str) -> None:
    print(f"  NG  {msg}", flush=True)


def info(msg: str) -> None:
    print(f"  ..  {msg}", flush=True)


def _decode(r: httpx.Response) -> Any:
    try:
        return r.json()
    except ValueError:
        return {"_raw": r.text}


def _check_dict(r: httpx.Response) -> dict[str, Any]:
    body = _decode(r)
    if not r.is_success:
        raise RuntimeError(
            f"HTTP {r.status_code} {r.reason_phrase} body={json.dumps(body, ensure_ascii=False)}"
        )
    if not isinstance(body, dict):
        return {"_raw": body}
    return body


def _check_list(r: httpx.Response) -> list[Any]:
    body = _decode(r)
    if not r.is_success:
        raise RuntimeError(
            f"HTTP {r.status_code} {r.reason_phrase} body={json.dumps(body, ensure_ascii=False)}"
        )
    if not isinstance(body, list):
        raise RuntimeError(f"想定外レスポンス (list 期待): {body!r}")
    return body


async def fetch_token(client: httpx.AsyncClient, ep: Endpoint, password: str) -> str:
    r = await client.post(
        f"{ep.http_base}/token",
        json={"APIPassword": password},
        headers={"Content-Type": "application/json"},
    )
    body = _check_dict(r)
    token = body.get("Token")
    if not isinstance(token, str) or not token:
        raise RuntimeError(f"Token 不在: {body}")
    return token


async def fetch_wallet(client: httpx.AsyncClient, ep: Endpoint, token: str) -> dict[str, Any]:
    r = await client.get(f"{ep.http_base}/wallet/cash", headers={"X-API-KEY": token})
    return _check_dict(r)


async def fetch_symbol(
    client: httpx.AsyncClient, ep: Endpoint, token: str, symbol: str, exchange: int
) -> dict[str, Any]:
    r = await client.get(
        f"{ep.http_base}/symbol/{symbol}@{exchange}",
        headers={"X-API-KEY": token},
    )
    return _check_dict(r)


async def fetch_board(
    client: httpx.AsyncClient, ep: Endpoint, token: str, symbol: str, exchange: int
) -> dict[str, Any]:
    r = await client.get(
        f"{ep.http_base}/board/{symbol}@{exchange}",
        headers={"X-API-KEY": token},
    )
    return _check_dict(r)


async def fetch_positions(
    client: httpx.AsyncClient, ep: Endpoint, token: str
) -> list[dict[str, Any]]:
    r = await client.get(
        f"{ep.http_base}/positions",
        params={"product": 1},  # 1=現物 のみ
        headers={"X-API-KEY": token},
    )
    body = _check_list(r)
    out: list[dict[str, Any]] = []
    for item in body:
        if isinstance(item, dict):
            out.append(item)
    return out


async def fetch_orders(
    client: httpx.AsyncClient,
    ep: Endpoint,
    token: str,
    *,
    order_id: str | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] | None = {"id": order_id} if order_id else None
    r = await client.get(
        f"{ep.http_base}/orders",
        params=params,
        headers={"X-API-KEY": token},
    )
    body = _check_list(r)
    out: list[dict[str, Any]] = []
    for item in body:
        if isinstance(item, dict):
            out.append(item)
    return out


def build_sendorder_payload(args: argparse.Namespace, order_password: str) -> dict[str, Any]:
    """probe スクリプト用の最小発注 payload。

    本物の発注ロジックは ``services/oms-live/src/oms_live/order_builder.py`` に
    あるが、本スクリプトは単独で動く必要があるためインライン実装する。
    現物前提 (CashMargin=1, SecurityType=1)。
    """
    if args.order_side == "buy":
        side_code = "2"
        deliv_type = 2
        fund_type = "AA"
    else:
        side_code = "1"
        deliv_type = 0
        fund_type = "  "

    if args.order_type == "market":
        front_order_type = 10
        price: float = 0.0
    else:
        front_order_type = 20
        price = float(args.order_price)
        if price <= 0:
            raise ValueError("--order-type limit 指定時は --order-price > 0 が必要")

    return {
        "Symbol": args.symbol,
        "Exchange": args.exchange,
        "SecurityType": 1,
        "Side": side_code,
        "CashMargin": 1,
        "DelivType": deliv_type,
        "AccountType": args.account_type,
        "FundType": fund_type,
        "Qty": args.order_qty,
        "FrontOrderType": front_order_type,
        "Price": price,
        "ExpireDay": 0,
        "Password": order_password,
    }


async def send_order(
    client: httpx.AsyncClient,
    ep: Endpoint,
    token: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    r = await client.post(
        f"{ep.http_base}/sendorder",
        json=payload,
        headers={"X-API-KEY": token, "Content-Type": "application/json"},
    )
    return _check_dict(r)


async def cancel_order(
    client: httpx.AsyncClient,
    ep: Endpoint,
    token: str,
    *,
    order_id: str,
    order_password: str,
) -> dict[str, Any]:
    r = await client.put(
        f"{ep.http_base}/cancelorder",
        json={"OrderId": order_id, "Password": order_password},
        headers={"X-API-KEY": token, "Content-Type": "application/json"},
    )
    return _check_dict(r)


async def main() -> int:
    args = parse_args()
    api_password = os.environ.get("KABU_API_PASSWORD")
    if not api_password:
        fail("環境変数 KABU_API_PASSWORD が未設定")
        return 2

    order_password: str | None = None
    if args.send:
        order_password = os.environ.get("KABU_ORDER_PASSWORD")
        if not order_password:
            fail("--send 指定時は環境変数 KABU_ORDER_PASSWORD も必要")
            return 2

    port = args.port if args.port is not None else PORT_BY_ENV[args.env]
    ep = Endpoint(host=args.host, port=port)
    host_header = args.host_header
    print(
        f"target: {ep.http_base}  (env={args.env}, host={args.host}, port={port}"
        f"{', host_header=' + host_header if host_header else ''})"
    )
    if args.send:
        print(
            f"!! --send 有効: {args.order_side.upper()} {args.symbol} x {args.order_qty} "
            f"({args.order_type})"
            f"{' + auto-cancel' if args.auto_cancel else ''}",
            flush=True,
        )

    client_headers: dict[str, str] = {}
    if host_header:
        client_headers["Host"] = host_header

    async with httpx.AsyncClient(timeout=10.0, headers=client_headers) as client:
        step("1. トークン取得")
        try:
            token = await fetch_token(client, ep, api_password)
        except Exception as e:
            fail(f"token 取得失敗: {e!r}")
            return 1
        ok(f"token={token[:8]}... (len={len(token)})")

        if "wallet" not in args.skip:
            step("2. 買付余力 (GET /wallet/cash)")
            try:
                wallet = await fetch_wallet(client, ep, token)
                ok(json.dumps(wallet, ensure_ascii=False))
            except Exception as e:
                fail(f"wallet 取得失敗: {e!r}")
                return 1

        if "symbol" not in args.skip:
            step(f"3. 銘柄情報 (GET /symbol/{args.symbol}@{args.exchange})")
            try:
                sym = await fetch_symbol(client, ep, token, args.symbol, args.exchange)
                ok(json.dumps(sym, ensure_ascii=False))
            except Exception as e:
                fail(f"symbol 取得失敗: {e!r}")
                return 1

        if "board" not in args.skip:
            step(f"4. 板情報 + 現在値 (GET /board/{args.symbol}@{args.exchange})")
            try:
                board = await fetch_board(client, ep, token, args.symbol, args.exchange)
                # 主要フィールドだけサマリ表示。検証環境 (18081) は market 終了後
                # CurrentPrice が None を返すことがある (kabuステーション仕様)
                summary = {
                    "CurrentPrice": board.get("CurrentPrice"),
                    "BidPrice": board.get("BidPrice"),
                    "AskPrice": board.get("AskPrice"),
                    "BidQty": board.get("BidQty"),
                    "AskQty": board.get("AskQty"),
                    "TradingVolume": board.get("TradingVolume"),
                    "TradingValue": board.get("TradingValue"),
                }
                ok(json.dumps(summary, ensure_ascii=False))
                # 詳細 (Sell1〜Sell10 / Buy1〜Buy10 等) はデバッグ用に info で出す
                info(f"raw={json.dumps(board, ensure_ascii=False)}")
            except Exception as e:
                fail(f"board 取得失敗: {e!r}")
                return 1

        if "positions" not in args.skip:
            step("5. 現物残高 (GET /positions?product=1)")
            try:
                positions = await fetch_positions(client, ep, token)
                ok(f"{len(positions)} 件")
                for pos in positions:
                    info(json.dumps(pos, ensure_ascii=False))
            except Exception as e:
                fail(f"positions 取得失敗: {e!r}")
                return 1

        if "orders" not in args.skip:
            step("6. 注文一覧 (GET /orders)")
            try:
                orders = await fetch_orders(client, ep, token)
                ok(f"{len(orders)} 件 (先頭 {args.orders_limit} 件のみ表示)")
                for od in orders[: args.orders_limit]:
                    info(json.dumps(od, ensure_ascii=False))
            except Exception as e:
                fail(f"orders 取得失敗: {e!r}")
                return 1

        if args.send:
            assert order_password is not None  # parse 段階で保証
            step("7. 発注 (POST /sendorder)")
            try:
                payload = build_sendorder_payload(args, order_password)
                # Password はログに出さない
                logged = {k: v for k, v in payload.items() if k != "Password"}
                info(f"payload={json.dumps(logged, ensure_ascii=False)}")
                resp = await send_order(client, ep, token, payload)
            except Exception as e:
                fail(f"sendorder 失敗: {e!r}")
                return 1
            ok(json.dumps(resp, ensure_ascii=False))

            order_id = resp.get("OrderId") if isinstance(resp.get("OrderId"), str) else None
            result_code = resp.get("Result")
            if result_code != 0 or not order_id:
                fail(f"sendorder Result != 0 or OrderId 不在: result={result_code}")
                return 1

            step(f"8. 発注後状態 (GET /orders?id={order_id})")
            try:
                states = await fetch_orders(client, ep, token, order_id=order_id)
                if not states:
                    info("該当 OrderId の行なし (反映遅延の可能性)")
                else:
                    for s in states:
                        ok(json.dumps(s, ensure_ascii=False))
            except Exception as e:
                fail(f"orders?id={order_id} 取得失敗: {e!r}")
                return 1

            if args.auto_cancel:
                step(f"9. 取消 (PUT /cancelorder OrderId={order_id})")
                try:
                    cancel_resp = await cancel_order(
                        client,
                        ep,
                        token,
                        order_id=order_id,
                        order_password=order_password,
                    )
                    ok(json.dumps(cancel_resp, ensure_ascii=False))
                except Exception as e:
                    fail(f"cancelorder 失敗: {e!r}")
                    return 1

    print("\nALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
