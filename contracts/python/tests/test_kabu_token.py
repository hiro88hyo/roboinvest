"""KabuTokenCache の単体テスト。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from trade_contracts.kabu_token import KabuTokenCache


@pytest.fixture()
def cache(tmp_path: Path) -> KabuTokenCache:
    return KabuTokenCache(tmp_path / "kabu_token_cache.json")


def test_load_returns_none_when_file_absent(cache: KabuTokenCache) -> None:
    assert cache.load() is None


def test_save_and_load_roundtrip(cache: KabuTokenCache) -> None:
    cache.save("test-token-abc123")
    assert cache.load() == "test-token-abc123"


def test_load_returns_none_after_invalidate(cache: KabuTokenCache) -> None:
    cache.save("tok")
    cache.invalidate()
    assert cache.load() is None


def test_invalidate_is_idempotent(cache: KabuTokenCache) -> None:
    cache.invalidate()
    cache.invalidate()  # should not raise


def test_save_overwrites_existing_token(cache: KabuTokenCache) -> None:
    cache.save("old-token")
    cache.save("new-token")
    assert cache.load() == "new-token"


def test_load_returns_none_on_corrupt_json(cache: KabuTokenCache, tmp_path: Path) -> None:
    (tmp_path / "kabu_token_cache.json").write_text("not-json{{{")
    assert cache.load() is None


def test_load_returns_none_when_token_field_missing(cache: KabuTokenCache, tmp_path: Path) -> None:
    (tmp_path / "kabu_token_cache.json").write_text(json.dumps({"other": "field"}))
    assert cache.load() is None


def test_load_returns_none_when_token_is_empty_string(
    cache: KabuTokenCache, tmp_path: Path
) -> None:
    (tmp_path / "kabu_token_cache.json").write_text(json.dumps({"token": ""}))
    assert cache.load() is None
