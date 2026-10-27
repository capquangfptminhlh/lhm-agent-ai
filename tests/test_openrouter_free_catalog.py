"""Strict catalog verification never uses real keys or makes network calls."""
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
import asyncio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from openrouter_free_catalog import eligible_free_models, is_zero_price, rank_for_role, select_free_model


class FreeCatalogTests(unittest.TestCase):
    def test_only_explicit_zero_prices_accepted(self):
        data = {"data": [
            {"id": "google/gemma-4-31b:free", "pricing": {"prompt": "0", "completion": "0", "request": "0"}},
            {"id": "nvidia/nemotron-3:free", "pricing": {"prompt": "0", "completion": "0"}},
            {"id": "openai/paid", "pricing": {"prompt": "0", "completion": "0"}},
            {"id": "example/paid:free", "pricing": {"prompt": "0", "completion": "0.000001"}},
            {"id": "example/unknown:free", "pricing": {"prompt": "0"}},
            {"id": "example/unknown2:free", "pricing": {"prompt": None, "completion": "0"}},
            {"id": "example/invalid:free", "pricing": {"prompt": "NaN", "completion": "0"}},
            {"id": "example/image:free", "pricing": {"prompt": "0", "completion": "0", "image": "0.001"}},
        ]}
        self.assertEqual(eligible_free_models(data), ["google/gemma-4-31b:free", "nvidia/nemotron-3:free"])
        self.assertFalse(is_zero_price({"prompt": True, "completion": "0"}))

    def test_role_preference_is_only_over_verified_candidates(self):
        models = ["google/gemma-4-31b:free", "nvidia/nemotron-3:free"]
        self.assertEqual(rank_for_role(models, "director")[0], "nvidia/nemotron-3:free")
        self.assertEqual(rank_for_role(models, "content")[0], "google/gemma-4-31b:free")

    def test_invalid_catalog_fails_closed(self):
        with self.assertRaises(ValueError):
            eligible_free_models({"models": []})

    def test_selection_does_not_guess_on_network_failure(self):
        with patch("openrouter_free_catalog.fetch_verified_text_models", new=AsyncMock(side_effect=RuntimeError("offline"))):
            with self.assertRaises(RuntimeError):
                asyncio.run(select_free_model("director"))


if __name__ == "__main__":
    unittest.main()
