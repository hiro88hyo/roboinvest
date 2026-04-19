"""統合テスト共通のフィクスチャ。

Pub/Sub エミュレータ (必須) と Supabase ローカル (任意) への到達性を確認し、
届かない環境では `pytest.skip` で丸ごとスキップする。
環境変数は `infra/.env` に従う:

- `PUBSUB_EMULATOR_HOST` (例: `localhost:8085`)
- `PUBSUB_PROJECT_ID` (例: `trade-ai-dev`)
- `SUPABASE_URL` (例: `http://127.0.0.1:54321`)
- `SUPABASE_SECRET_KEY`
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from uuid import uuid4

import httpx
import pytest

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def pubsub_emulator_host() -> str:
    host = os.environ.get("PUBSUB_EMULATOR_HOST")
    if not host:
        pytest.skip("PUBSUB_EMULATOR_HOST not set")
    try:
        resp = httpx.get(f"http://{host}/", timeout=2.0)
    except httpx.HTTPError as exc:
        pytest.skip(f"pubsub emulator unreachable at {host}: {exc}")
    # エミュレータはルートに 404 を返すが到達できれば OK
    if resp.status_code >= 500:
        pytest.skip(f"pubsub emulator unhealthy: status={resp.status_code}")
    return host


@pytest.fixture(scope="session")
def pubsub_project_id() -> str:
    project = os.environ.get("PUBSUB_PROJECT_ID")
    if not project:
        pytest.skip("PUBSUB_PROJECT_ID not set")
    return project


@pytest.fixture(scope="session")
def supabase_url() -> str:
    url = os.environ.get("SUPABASE_URL")
    if not url:
        pytest.skip("SUPABASE_URL not set")
    try:
        resp = httpx.get(f"{url.rstrip('/')}/rest/v1/", timeout=2.0)
    except httpx.HTTPError as exc:
        pytest.skip(f"supabase unreachable at {url}: {exc}")
    if resp.status_code >= 500:
        pytest.skip(f"supabase unhealthy: status={resp.status_code}")
    return url


@pytest.fixture(scope="session")
def supabase_secret_key() -> str:
    key = os.environ.get("SUPABASE_SECRET_KEY")
    if not key:
        pytest.skip("SUPABASE_SECRET_KEY not set")
    return key


@pytest.fixture
def run_id() -> str:
    """テストごとに一意な suffix。Pub/Sub の topic / subscription 名の衝突を避ける。"""
    return uuid4().hex[:8]


@pytest.fixture
async def pubsub_admin(
    pubsub_emulator_host: str,
) -> AsyncIterator[httpx.AsyncClient]:
    """Pub/Sub エミュレータの管理 REST を叩く httpx クライアント (topic/subscription 管理用)。"""
    async with httpx.AsyncClient(
        base_url=f"http://{pubsub_emulator_host}",
        timeout=10.0,
        headers={"Content-Type": "application/json"},
    ) as client:
        yield client


@pytest.fixture
def test_symbol(run_id: str) -> str:
    """既存データと衝突しない一時テスト銘柄。"""
    return f"IT{run_id[:6].upper()}"


@pytest.fixture
def unique_resources(run_id: str) -> Iterator[dict[str, str]]:
    """テストで使う一意な subscription 名を払い出す。"""
    yield {
        "raw_sub": f"it-raw-{run_id}",
        "features_sub": f"it-features-{run_id}",
    }
