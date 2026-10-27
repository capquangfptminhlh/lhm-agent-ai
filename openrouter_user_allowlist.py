"""User-approved OpenRouter free models from three screenshots (2026-10-27).

Catalog IDs must be explicitly :free, prices must be verifiably zero, and
the provider/name must correspond to a model selected by the owner.
"""
from __future__ import annotations
import re
from typing import Any

# Catalog display names may include publisher and '(free)' decorations.
# Require both publisher ID prefix and an unambiguous model-name pattern.
APPROVED = (
    ("recursive/", r"flint"),
    ("xingchen-agi/", r"xing\s*4[.]0.*29b.*a4b"),
    ("nvidia/", r"nemotron\s*3[.]5\s*super\s*vl"),
    ("dots-studio/", r"dots3[- ]note"),
    ("liquid/", r"lfm2[.]5[- ]2[.]6b"),
    ("nvidia/", r"nemotron\s*3[.]5\s*lightning"),
    ("thinkingmachines/", r"inkling\s*small"),
    ("poolside/", r"laguna\s*s\s*2[.]1"),
    ("thinkingmachines/", r"inkling"),
    ("poolside/", r"laguna\s*xs\s*2[.]1"),
    ("cohere/", r"north\s*mini\s*code"),
    ("nvidia/", r"nemotron\s*3[.]5\s*content\s*safety"),
    ("nvidia/", r"nemotron\s*3\s*ultra"),
    ("google/", r"gemma\s*4\s*26b\s*a4b"),
    ("google/", r"gemma\s*4\s*31b"),
)


def approved_catalog_item(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    model_id, name = item.get("id"), item.get("name")
    if not isinstance(model_id, str) or not model_id.endswith(":free"):
        return False
    if not isinstance(name, str):
        return False
    name = name.casefold().split("(free)")[0]
    # Exact expected IDs are generated from OpenRouter, never screenshots.
    for prefix, pattern in APPROVED:
        if model_id.startswith(prefix) and re.search(pattern, name, re.I):
            # Inkling and Laguna patterns are distinguished by provider,
            # but generic Inkling shouldn't match arbitrary Inkling variants.
            if pattern == "inkling" and "small" in name:
                continue
            return True
    return False


def filter_approved_models(catalog: Any) -> list[str]:
    from openrouter_free_catalog import is_zero_price
    if not isinstance(catalog, dict) or not isinstance(catalog.get("data"), list):
        raise ValueError("Invalid OpenRouter catalog")
    return sorted({
        item["id"] for item in catalog["data"]
        if approved_catalog_item(item) and is_zero_price(item.get("pricing"))
    })
