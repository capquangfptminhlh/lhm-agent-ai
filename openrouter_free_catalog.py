"""Fail-closed discovery of free models from the official OpenRouter catalog.

Model labels and screenshots are never an authority for pricing. This module
accepts *only* exact IDs ending in ':free' with every returned pricing field
explicitly equal to zero. Unknown or nonnumeric price => reject.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any
import httpx

CATALOG_URL = "https://openrouter.ai/api/v1/models"
ROLE_HINTS = {
    "research": ("gemma", "nemotron", "xingchen"),
    "director": ("nemotron", "gemma"),
    "strategy": ("nemotron", "gemma"),
    "content": ("gemma", "nemotron"),
    "quality": ("nemotron", "gemma"),
    "compliance": ("nemotron", "gemma"),
    "analytics": ("nemotron", "gemma"),
    "operations": ("nemotron", "gemma"),
    "support": ("gemma", "nemotron"),
}
# OpenRouter returned pricing may include request, image, cache rates.
# Reject an item if ANY advertised monetary field is positive or unknown.
def is_zero_price(pricing: Any) -> bool:
    if not isinstance(pricing, dict) or not pricing:
        return False
    if "prompt" not in pricing or "completion" not in pricing:
        return False
    for value in pricing.values():
        if isinstance(value, bool) or value is None:
            return False
        try:
            number = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return False
        if not number.is_finite() or number != 0:
            return False
    return True


def eligible_free_models(catalog: Any) -> list[str]:
    if not isinstance(catalog, dict) or not isinstance(catalog.get("data"), list):
        raise ValueError("Invalid OpenRouter model catalog")
    result = []
    for item in catalog["data"]:
        if not isinstance(item, dict):
            continue
        model = item.get("id")
        if (isinstance(model, str) and model.endswith(":free")
                and model.count("/") == 1 and len(model) <= 128
                and is_zero_price(item.get("pricing"))):
            result.append(model)
    return sorted(set(result))


def rank_for_role(models: list[str], role: str) -> list[str]:
    hints = ROLE_HINTS.get(role, ("nemotron", "gemma", "xingchen"))
    return sorted(models, key=lambda model: (
        next((i for i, hint in enumerate(hints) if hint in model.lower()), len(hints)),
        model,
    ))


async def fetch_verified_models(*, timeout: float = 10) -> list[str]:
    # No fallback to a cached catalog or a guessed paid model on errors.
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        response = await client.get(CATALOG_URL)
        response.raise_for_status()
        from openrouter_user_allowlist import filter_approved_models
        models = filter_approved_models(response.json())
    if not models:
        raise ValueError("No verified zero-priced :free models available")
    return models


async def select_free_model(role: str) -> str:
    return rank_for_role(await fetch_verified_models(), role)[0]
