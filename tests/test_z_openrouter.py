"""Offline coverage for OpenRouter free-first, quota, privacy and billing.

No live OpenRouter or DeepSeek requests are made by these tests.
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class FreeRouterTest(unittest.TestCase):
    def setUp(self):
        # Import lazily: existing test_system configures a temporary SQLite DB
        # during unittest discovery before any tests start.
        import app
        import core
        import openrouter_provider
        self.app = app
        self.core = core
        self.router = openrouter_provider

    def test_only_free_openrouter_models_accepted(self):
        self.assertTrue(self.router.is_free_model("openrouter/free"))
        self.assertTrue(self.router.is_free_model("nvidia/nemotron-3:free"))
        self.assertFalse(self.router.is_free_model("openai/gpt-4"))
        self.assertFalse(self.router.is_free_model("openrouter/auto"))
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as err:
            self.router.validate_model("openrouter/auto")
        self.assertEqual(err.exception.status_code, 422)

    def test_free_model_records_zero_cost_not_deepseek_rates(self):
        usage = {"prompt_tokens": 5000, "completion_tokens": 1000}
        self.assertEqual(self.core.recorded_cost(usage, "openrouter/free"), (0, 5000, 1000, 0.0))
        self.assertEqual(self.core.recorded_cost(usage, "nvidia/nemotron-3:free")[-1], 0.0)
        self.assertGreater(self.core.recorded_cost(usage, "deepseek-flash")[-1], 0)

    def test_provider_status_without_key_leakage(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "local-test-secret"}):
            data = self.app.llm_providers()
        self.assertTrue(data["providers"]["openrouter"]["configured"])
        self.assertNotIn("local-test-secret", str(data))
        self.assertTrue(data["no_paid_fallback"])
        self.assertEqual(data["quota_source"], "local_only")

    def test_free_response_shape_and_key_server_side(self):
        import httpx
        seen = {}
        payload = {"choices":[{"message":{"content":"Bản nháp đã soạn"}}],
                   "usage":{"prompt_tokens":110,"completion_tokens":70,"cost":0}}
        class Client:
            def __init__(self, *args, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def post(self, url, headers=None, json=None):
                seen.update(url=url, headers=headers, body=json)
                return httpx.Response(200, json=payload, request=httpx.Request("POST", url))
        with patch.dict(os.environ, {"OPENROUTER_API_KEY":"unit-secret"}):
            with patch.object(self.router.httpx, "AsyncClient", Client):
                answer = asyncio.run(self.router.call_free_model(
                    "content", {"industry":"general","business_context":""},
                    "Viết nội dung quảng cáo hợp lệ", "", "openrouter/free"))
        self.assertEqual(answer["choices"][0]["message"]["content"], "Bản nháp đã soạn")
        self.assertEqual(seen["body"]["model"], "openrouter/free")
        self.assertEqual(seen["headers"]["Authorization"], "Bearer unit-secret")
        self.assertEqual(seen["url"], self.router.OPENROUTER_URL)

    def test_free_model_rejects_common_personal_contacts(self):
        from fastapi import HTTPException
        with patch.dict(os.environ, {"OPENROUTER_API_KEY":"unit-secret",
                                     "LHM_OPENROUTER_ALLOW_CONTACT_DATA":"0"}):
            with self.assertRaises(HTTPException) as err:
                asyncio.run(self.router.call_free_model(
                    "content", {"industry":"general","business_context":""},
                    # A real mailbox domain: RFC 2606 example.com is test-only
                    # and allowed since the owner P4 run (task 4).
                    "Gửi phản hồi cho khach.hang@gmail.com",
                    "", "openrouter/free"))
        self.assertEqual(err.exception.status_code, 422)

    def test_openrouter_429_no_paid_fallback(self):
        from fastapi import HTTPException
        import httpx
        class Client:
            def __init__(self, *args, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def post(self, url, **kwargs):
                return httpx.Response(429,json={"error":{"message":"limit"}},
                                      request=httpx.Request("POST", url))
        with patch.dict(os.environ, {"OPENROUTER_API_KEY":"unit-secret"}):
            with patch.object(self.router.httpx, "AsyncClient", Client):
                with self.assertRaises(HTTPException) as err:
                    asyncio.run(self.router.call_free_model(
                        "content", {"industry":"general","business_context":""},
                        "Lập kế hoạch bài viết", "", "openrouter/free"))
        self.assertEqual(err.exception.status_code, 429)
        self.assertIn("không gọi model trả phí", err.exception.detail)

    def test_quota_counts_attempts_and_defers_next_task(self):
        from fastapi.testclient import TestClient
        client = TestClient(self.app.app)
        workspace_id = "free_" + self.core.new_id().replace("-", "")[:15]
        new = client.post("/workspaces", json={"id":workspace_id,"name":"OpenRouter Pilot",
                         "industry":"general","budget_usd":0.01})
        self.assertEqual(new.status_code, 201, new.text)
        with patch.dict(os.environ, {
            "OPENROUTER_API_KEY":"unit-secret",
            "LHM_OPENROUTER_24H_LIMIT":"1",
            "LHM_OPENROUTER_1M_LIMIT":"1",
        }):
            with self.core.db() as c:
                a = self.app.task_record(c, workspace_id, "content",
                    "Viết bài giới thiệu sản phẩm", "", model="openrouter/free")
                b = self.app.task_record(c, workspace_id, "research",
                    "Nghiên cứu xu hướng thị trường", "", model="openrouter/free")
            first = self.app.claim_task(a["id"])
            self.assertEqual(first["model"], "openrouter/free")
            delayed = self.app.claim_task(b["id"])
            self.assertTrue(delayed["quota_delayed"])
            with self.core.db() as c:
                later = c.execute("SELECT status,next_run_at FROM tasks WHERE id=?",
                                  (b["id"],)).fetchone()
                self.assertEqual(later["status"], "queued")
                self.assertGreater(later["next_run_at"], self.core.now())
                self.assertEqual(self.router.local_free_quota(c,self.core.now())["used_24h"], 1)
            # Clean test attempt so unrelated tests are not quota-bound.
            with self.core.db() as c:
                c.execute("DELETE FROM openrouter_attempts")


if __name__ == "__main__":
    unittest.main()
