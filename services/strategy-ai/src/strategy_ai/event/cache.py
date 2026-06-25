from __future__ import annotations

import hashlib
from decimal import Decimal


def event_ai_cache_key(
    *,
    prompt_hash: str,
    model_provider: str,
    model_id: str,
    temperature: Decimal,
    seed: int | None,
) -> str:
    raw = f"{prompt_hash}:{model_provider}:{model_id}:{temperature}:{seed}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
