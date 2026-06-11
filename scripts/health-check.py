#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "httpx>=0.27",
# ]
# ///
"""ローカル開発環境のヘルスチェック CLI。

3 つのセクションを env 駆動で実行し、開発スタックの状態を一括確認する。

  1. Pub/Sub: ``infra/pubsub/topics.json`` に列挙された全トピックと
     ``infra/pubsub/subscriptions.json`` に列挙された全サブスクリプションが
     エミュレータ上に存在するか
  2. Supabase: ``contracts/sql/`` 由来の主要テーブルが PostgREST 経由で
     可読か (``select=...&limit=0`` で空 200 を期待)
  3. Services: 各サービスの ``python -m <pkg> --help`` が returncode=0 で
     起動できるか (CLI が壊れていないかの煙テスト)

env が未設定のセクションは ``SKIP`` 扱い (NG ではない)。NG が 1 件でもあれば
終了コード 1 を返す。

使い方:
    uv run scripts/health-check.py
    uv run scripts/health-check.py --check pubsub supabase
    uv run scripts/health-check.py --check services --timeout 30
    PUBSUB_EMULATOR_HOST=localhost:8085 PUBSUB_PROJECT_ID=trade-ai-dev \\
        uv run scripts/health-check.py --check pubsub

詳細な env 一覧は ``--help`` を参照。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
TOPICS_JSON = REPO_ROOT / "infra" / "pubsub" / "topics.json"
SUBSCRIPTIONS_JSON = REPO_ROOT / "infra" / "pubsub" / "subscriptions.json"

SUPABASE_TABLES: tuple[str, ...] = (
    "system_status",
    "positions",
    "strategy_logs",
    "aggregator_logs",
    "trades_live",
    "trades_paper",
    "watchlist",
    "master_stocks",
    "daily_ohlcv",
    "market_regime",
)

SERVICE_MODULES: tuple[str, ...] = (
    "universe_scanner",
    "feature_engine",
    "strategy_rule",
    "strategy_ai",
    "aggregator",
    "gateway",
    "oms_paper",
    "oms_live",
    "feeder",
)

CHECK_NAMES = ("pubsub", "supabase", "services")


@dataclass(slots=True)
class CheckResult:
    name: str
    items: list[tuple[str, str, str]] = field(default_factory=list)  # (status, label, detail)

    @property
    def has_ng(self) -> bool:
        return any(status == "NG" for status, _, _ in self.items)

    @property
    def all_skipped(self) -> bool:
        return bool(self.items) and all(status == "SKIP" for status, _, _ in self.items)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--check",
        nargs="+",
        choices=[*CHECK_NAMES, "all"],
        default=["all"],
        help="実行するセクション (default: all)",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="HTTP / subprocess タイムアウト秒 (default: 15.0)",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="OK 行を抑制し NG / SKIP のみ表示",
    )
    return p.parse_args()


def step(label: str) -> None:
    print(f"\n=== {label} ===", flush=True)


def _emit(status: str, label: str, detail: str, *, quiet: bool) -> None:
    if quiet and status == "OK":
        return
    suffix = f" ({detail})" if detail else ""
    print(f"  {status:<4} {label}{suffix}", flush=True)


def _load_topics() -> list[str]:
    payload = json.loads(TOPICS_JSON.read_text(encoding="utf-8"))
    topics = payload.get("topics", [])
    if not isinstance(topics, list):
        raise RuntimeError(f"unexpected topics.json shape: {type(topics).__name__}")
    return [str(t) for t in topics]


def _load_subscriptions() -> list[dict[str, str]]:
    payload = json.loads(SUBSCRIPTIONS_JSON.read_text(encoding="utf-8"))
    subs = payload.get("subscriptions", [])
    if not isinstance(subs, list):
        raise RuntimeError(f"unexpected subscriptions.json shape: {type(subs).__name__}")
    return [{"name": str(s["name"]), "topic": str(s["topic"])} for s in subs]


async def check_pubsub(timeout: float, *, quiet: bool) -> CheckResult:
    step("Pub/Sub topics + subscriptions")
    result = CheckResult(name="pubsub")
    host = os.environ.get("PUBSUB_EMULATOR_HOST", "").strip()
    project = os.environ.get("PUBSUB_PROJECT_ID", "").strip()
    if not host or not project:
        detail = "PUBSUB_EMULATOR_HOST / PUBSUB_PROJECT_ID 未設定"
        _emit("SKIP", "section", detail, quiet=quiet)
        result.items.append(("SKIP", "section", detail))
        return result

    try:
        topics = _load_topics()
    except (OSError, ValueError, RuntimeError) as exc:
        detail = f"topics.json 読込失敗: {exc!r}"
        _emit("NG", "section", detail, quiet=quiet)
        result.items.append(("NG", "section", detail))
        return result

    try:
        subscriptions = _load_subscriptions()
    except (OSError, ValueError, RuntimeError) as exc:
        detail = f"subscriptions.json 読込失敗: {exc!r}"
        _emit("NG", "section", detail, quiet=quiet)
        result.items.append(("NG", "section", detail))
        return result

    async with httpx.AsyncClient(timeout=timeout) as client:
        base_topics = f"http://{host}/v1/projects/{project}/topics"
        for topic in topics:
            try:
                r = await client.get(f"{base_topics}/{topic}")
            except httpx.HTTPError as exc:
                _emit("NG", topic, repr(exc), quiet=quiet)
                result.items.append(("NG", topic, repr(exc)))
                continue
            if r.status_code == 200:
                _emit("OK", topic, "", quiet=quiet)
                result.items.append(("OK", topic, ""))
            elif r.status_code == 404:
                _emit("NG", topic, "存在しない", quiet=quiet)
                result.items.append(("NG", topic, "存在しない"))
            else:
                detail = f"HTTP {r.status_code} body={r.text[:120]}"
                _emit("NG", topic, detail, quiet=quiet)
                result.items.append(("NG", topic, detail))

        base_subs = f"http://{host}/v1/projects/{project}/subscriptions"
        for sub in subscriptions:
            name, topic = sub["name"], sub["topic"]
            label = f"sub:{name}"
            try:
                r = await client.get(f"{base_subs}/{name}")
            except httpx.HTTPError as exc:
                _emit("NG", label, repr(exc), quiet=quiet)
                result.items.append(("NG", label, repr(exc)))
                continue
            if r.status_code == 200:
                _emit("OK", label, f"-> {topic}", quiet=quiet)
                result.items.append(("OK", label, f"-> {topic}"))
            elif r.status_code == 404:
                _emit("NG", label, f"存在しない (topic={topic})", quiet=quiet)
                result.items.append(("NG", label, f"存在しない (topic={topic})"))
            else:
                detail = f"HTTP {r.status_code} body={r.text[:120]}"
                _emit("NG", label, detail, quiet=quiet)
                result.items.append(("NG", label, detail))

    return result


async def check_supabase(timeout: float, *, quiet: bool) -> CheckResult:
    step("Supabase tables")
    result = CheckResult(name="supabase")
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SECRET_KEY", "").strip()
    if not url or not key:
        detail = "SUPABASE_URL / SUPABASE_SECRET_KEY 未設定"
        _emit("SKIP", "section", detail, quiet=quiet)
        result.items.append(("SKIP", "section", detail))
        return result

    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    async with httpx.AsyncClient(
        base_url=url.rstrip("/"), headers=headers, timeout=timeout
    ) as client:
        for table in SUPABASE_TABLES:
            try:
                r = await client.get(f"/rest/v1/{table}", params={"select": "*", "limit": 0})
            except httpx.HTTPError as exc:
                _emit("NG", table, repr(exc), quiet=quiet)
                result.items.append(("NG", table, repr(exc)))
                continue
            if r.status_code == 200:
                _emit("OK", table, "", quiet=quiet)
                result.items.append(("OK", table, ""))
            else:
                detail = f"HTTP {r.status_code} body={r.text[:120]}"
                _emit("NG", table, detail, quiet=quiet)
                result.items.append(("NG", table, detail))
    return result


async def check_services(timeout: float, *, quiet: bool) -> CheckResult:
    step("Service CLI --help")
    result = CheckResult(name="services")

    async def _probe(module: str) -> tuple[str, str, str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "uv",
                "run",
                "python",
                "-m",
                module,
                "--help",
                cwd=str(REPO_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError:
            return ("NG", module, "uv コマンドが見つからない")
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return ("NG", module, f"timeout {timeout}s")
        if proc.returncode != 0:
            tail = (stderr_b or stdout_b).decode(errors="replace").strip().splitlines()
            detail = f"returncode={proc.returncode} {tail[-1] if tail else ''}"
            return ("NG", module, detail)
        if b"usage:" not in stdout_b.lower():
            return ("NG", module, "stdout に 'usage:' が無い")
        return ("OK", module, "")

    # subprocess は I/O bound なので並列に投げる (uv のロック競合は uv 側で吸収)
    probes = [_probe(m) for m in SERVICE_MODULES]
    for outcome in await asyncio.gather(*probes):
        status, label, detail = outcome
        _emit(status, label, detail, quiet=quiet)
        result.items.append(outcome)
    return result


def _selected_checks(raw: Iterable[str]) -> list[str]:
    selected = list(raw)
    if "all" in selected:
        return list(CHECK_NAMES)
    # 入力順 → CHECK_NAMES 順に正規化
    return [name for name in CHECK_NAMES if name in selected]


async def main() -> int:
    args = parse_args()
    selected = _selected_checks(args.check)

    results: list[CheckResult] = []
    if "pubsub" in selected:
        results.append(await check_pubsub(args.timeout, quiet=args.quiet))
    if "supabase" in selected:
        results.append(await check_supabase(args.timeout, quiet=args.quiet))
    if "services" in selected:
        results.append(await check_services(args.timeout, quiet=args.quiet))

    print("\n=== summary ===", flush=True)
    exit_code = 0
    for r in results:
        ok = sum(1 for s, _, _ in r.items if s == "OK")
        ng = sum(1 for s, _, _ in r.items if s == "NG")
        skip = sum(1 for s, _, _ in r.items if s == "SKIP")
        verdict = "NG" if r.has_ng else ("SKIP" if r.all_skipped else "OK")
        print(f"  {verdict:<4} {r.name:<9} ok={ok} ng={ng} skip={skip}", flush=True)
        if r.has_ng:
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
