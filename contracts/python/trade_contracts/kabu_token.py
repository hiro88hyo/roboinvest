"""Shared file-based token cache for kabuステーション API.

Feeder と OMS Live は同じ kabustation インスタンスを共有する。kabustation は
API パスワード 1 本につきトークン 1 本の仕様で、POST /token を叩くと先発の
トークンが無効化される。このキャッシュにより、先に起動したサービスがトークンを
ファイルに書き出し、後発のサービスがそのトークンを読み込んで使い回すことで
POST /token の衝突を防ぐ。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class KabuTokenCache:
    """ファイルベースのトークンキャッシュ。

    Feeder と OMS Live が同じパスを指すように env ``KABU_TOKEN_CACHE_FILE``
    で統一する (デフォルト ``/tmp/kabu_token_cache.json``)。
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> str | None:
        """キャッシュファイルからトークンを読む。なければ / 読めなければ None。"""
        try:
            data = json.loads(self._path.read_text())
            token = data.get("token")
            return token if isinstance(token, str) and token else None
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, OSError) as exc:
            logger.debug("kabu token cache read error: %s", exc)
            return None

    def save(self, token: str) -> None:
        """トークンをファイルにアトミックに書き込む (tmp -> rename)。"""
        tmp = self._path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps({"token": token}))
            tmp.replace(self._path)
        except OSError as exc:
            logger.warning("kabu token cache write error: %s", exc)

    def invalidate(self) -> None:
        """キャッシュファイルを削除する。"""
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.debug("kabu token cache invalidate error: %s", exc)
