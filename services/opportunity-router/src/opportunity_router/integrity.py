"""Canonical JSON and SHA-256 helpers used by the Phase 1 router."""

from __future__ import annotations

import hashlib
import json


def canonical_json(payload: object) -> str:
    """Serialize a JSON-compatible value deterministically."""

    return json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_sha256(payload: object) -> str:
    """Hash a JSON-compatible value after canonical serialization."""

    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def verify_canonical_payload(payload_json: str, declared_sha256: str) -> bool:
    """Verify that payload text is canonical JSON and matches its declared hash."""

    try:
        payload: object = json.loads(payload_json)
    except json.JSONDecodeError:
        return False
    if canonical_json(payload) != payload_json:
        return False
    return hashlib.sha256(payload_json.encode("utf-8")).hexdigest() == declared_sha256
