"""Free-only Windows configuration and backend rejection contract tests."""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

class DesktopFreeOnlyTests(unittest.TestCase):
    def test_paid_model_rejected_before_network_even_with_paid_key_available(self):
        import app
        from openrouter_provider import validate_model
        env = {"LHM_OPENROUTER_FREE_ONLY": "1",
               "OPENROUTER_API_KEY": "ci-free-key",
               "DEEPSEEK_API_KEY": "ci-paid-key"}
        with patch.dict(os.environ, env):
            for model in ["deepseek-flash", "deepseek-v4-pro",
                          "openai/gpt-4", "openrouter/auto"]:
                with self.subTest(model=model):
                    with self.assertRaises(HTTPException) as caught:
                        validate_model(model)
                    self.assertEqual(caught.exception.status_code, 422)
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(app.deepseek_request("content",
                    {"industry": "general", "business_context": ""},
                    "hello", "", "deepseek-flash"))
            self.assertEqual(caught.exception.status_code, 422)

    def test_complex_task_uses_free_router_without_pro_key(self):
        import app
        from core import db, new_id
        from fastapi.testclient import TestClient
        client = TestClient(app.app)
        wid = "freedesktop_" + new_id().replace("-", "")[:12]
        with patch.dict(os.environ, {
            "LHM_OPENROUTER_FREE_ONLY": "1",
            "LHM_LLM_PROVIDER": "openrouter",
            "LHM_ALLOW_PRO": "0",
            "DEEPSEEK_API_KEY": "intentionally-paid-key",
        }):
            response = client.post("/workspaces", json={
                "id": wid, "name": "Free desktop testing",
                "industry": "general", "budget_usd": 0.01,
            })
            self.assertEqual(response.status_code, 201, response.text)
            task = client.post(f"/workspaces/{wid}/tasks", json={
                "role": "director",
                "instruction": "Phân tích chiến lược kinh doanh từ dữ liệu đã cho",
                "complexity": "high",
            })
            self.assertEqual(task.status_code, 201, task.text)
            with db() as connection:
                row = connection.execute("SELECT model FROM tasks WHERE id=?",
                                         (task.json()["id"],)).fetchone()
            self.assertEqual(row["model"], "openrouter/free")

    def test_only_router_or_explicit_free_model_can_enter_runtime(self):
        from openrouter_provider import validate_model
        with patch.dict(os.environ, {"LHM_OPENROUTER_FREE_ONLY": "1"}):
            for model in ["openrouter/free", "nvidia/nemotron-3-ultra:free"]:
                validate_model(model)

if __name__ == "__main__":
    unittest.main()
