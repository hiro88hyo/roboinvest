#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "httpx>=0.27",
#   "websockets>=13",
# ]
# ///
"""kabuステーション API 疎通スクリプト。

市場時間外（土日・夜間）でも以下まで確認できる:
  1. トークン取得 (POST /kabusapi/token)
  2. 口座残高取得 (GET /kabusapi/wallet/cash)
  3. 銘柄情報取得 (GET /kabusapi/symbol/{code}@{exchange})
  4. WebSocket ハンドシェイク (ws://.../kabusapi/websocket)

PUSH 配信本体の確認は平日 9:00 以降（市場時間）に別途必要。

使い方:
  KABU_API_PASSWORD=xxx uv run scripts/probe-kabu.py --env test
  KABU_API_PASSWORD=xxx uv run scripts/probe-kabu.py --env prod --host 192.168.1.10
  KABU_API_PASSWORD=xxx uv run scripts/probe-kabu.py --skip ws --symbol 9984
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass

import httpx
import websockets

PORT_BY_ENV = {"prod": 18080, "test": 18081}


@dataclass
class Endpoint:
    host: str
    port: int

    @property
    def http_base(self) -> str:
        return f"http://{self.host}:{self.port}/kabusapi"

    @property
    def ws_url(self) -> str:
        return f"ws://{self.host}:{self.port}/kabusapi/websocket"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--env", choices=["prod", "test"], default="test", help="本番(18080) or 検証(18081)")
    p.add_argument("--host", default="localhost", help="kabuステーションが動いているホスト")
    p.add_argument(
        "--host-header",
        default=None,
        help=(
            "HTTP/WS の Host ヘッダを上書き（既定は --host と同じ）。"
            "LAN 越しに繋ぐと Windows http.sys が Host: localhost しか受け付けず "
            "400 'Invalid Hostname' を返すため、その場合は `--host-header localhost` を指定する。"
        ),
    )
    p.add_argument("--symbol", default="7203", help="銘柄情報取得に使う証券コード (default: 7203 トヨタ)")
    p.add_argument("--exchange", default="1", help="市場コード (1=東証, 3=名証, default: 1)")
    p.add_argument(
        "--skip",
        nargs="*",
        choices=["wallet", "symbol", "ws"],
        default=[],
        help="スキップしたいチェック",
    )
    p.add_argument("--ws-timeout", type=float, default=3.0, help="WebSocket 接続維持秒数")
    return p.parse_args()


def step(label: str) -> None:
    print(f"\n=== {label} ===", flush=True)


def ok(msg: str) -> None:
    print(f"  OK  {msg}", flush=True)


def fail(msg: str) -> None:
    print(f"  NG  {msg}", flush=True)


def _check(r: httpx.Response) -> dict:
    """kabuステーションは 4xx でも JSON ボディに Code/Message を返してくるので、
    raise_for_status() で握りつぶさず必ず本文を露出させる。"""
    try:
        body = r.json()
    except ValueError:
        body = {"_raw": r.text}
    if r.is_success:
        return body
    raise RuntimeError(f"HTTP {r.status_code} {r.reason_phrase} body={json.dumps(body, ensure_ascii=False)}")


async def fetch_token(client: httpx.AsyncClient, ep: Endpoint, password: str) -> str:
    r = await client.post(
        f"{ep.http_base}/token",
        json={"APIPassword": password},
        headers={"Content-Type": "application/json"},
    )
    body = _check(r)
    token = body.get("Token")
    if not token:
        raise RuntimeError(f"Token 不在: {body}")
    return str(token)


async def fetch_wallet(client: httpx.AsyncClient, ep: Endpoint, token: str) -> dict:
    r = await client.get(f"{ep.http_base}/wallet/cash", headers={"X-API-KEY": token})
    return _check(r)


async def fetch_symbol(
    client: httpx.AsyncClient, ep: Endpoint, token: str, symbol: str, exchange: str
) -> dict:
    r = await client.get(
        f"{ep.http_base}/symbol/{symbol}@{exchange}",
        headers={"X-API-KEY": token},
    )
    return _check(r)


async def probe_websocket(ep: Endpoint, timeout: float, host_header: str | None) -> str | None:
    extra_headers = [("Host", host_header)] if host_header else None
    async with websockets.connect(
        ep.ws_url, open_timeout=5, additional_headers=extra_headers
    ) as ws:
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
            return str(msg)[:200]
        except asyncio.TimeoutError:
            return None


async def main() -> int:
    args = parse_args()
    password = os.environ.get("KABU_API_PASSWORD")
    if not password:
        fail("環境変数 KABU_API_PASSWORD が未設定")
        return 2

    ep = Endpoint(host=args.host, port=PORT_BY_ENV[args.env])
    host_header = args.host_header
    print(
        f"target: {ep.http_base}  (env={args.env}, host={args.host}"
        f"{', host_header=' + host_header if host_header else ''})"
    )

    client_headers: dict[str, str] = {}
    if host_header:
        client_headers["Host"] = host_header

    async with httpx.AsyncClient(timeout=10.0, headers=client_headers) as client:
        step("1. トークン取得")
        try:
            token = await fetch_token(client, ep, password)
        except Exception as e:
            fail(f"token 取得失敗: {e!r}")
            return 1
        ok(f"token={token[:8]}... (len={len(token)})")

        if "wallet" not in args.skip:
            step("2. 口座残高取得")
            try:
                wallet = await fetch_wallet(client, ep, token)
                ok(json.dumps(wallet, ensure_ascii=False))
            except Exception as e:
                fail(f"wallet 取得失敗: {e!r}")
                return 1

        if "symbol" not in args.skip:
            step(f"3. 銘柄情報取得 ({args.symbol}@{args.exchange})")
            try:
                sym = await fetch_symbol(client, ep, token, args.symbol, args.exchange)
                ok(json.dumps(sym, ensure_ascii=False))
            except Exception as e:
                fail(f"symbol 取得失敗: {e!r}")
                return 1

        if "ws" not in args.skip:
            step("4. WebSocket 疎通")
            try:
                msg = await probe_websocket(ep, args.ws_timeout, host_header)
                if msg is None:
                    ok(f"接続成立（{args.ws_timeout}s 内に push なし。市場時間外のため正常）")
                else:
                    ok(f"push 受信: {msg}")
            except Exception as e:
                fail(f"ws 接続失敗: {e!r}")
                return 1

    print("\nALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
