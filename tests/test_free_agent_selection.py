"""Regression: desktop Free-only calls use catalog-verified text models, never random router.

Offline only. Real Free API acceptance is tracked separately.
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import openrouter_provider as gateway

class RoleFreeReliabilityTests(unittest.TestCase):
    def test_free_only_forces_verified_text_selection_even_when_auto_off(self):
        seen={}
        candidate="nvidia/nemotron-3-ultra-550b-a55b:free"
        class Client:
            def __init__(self,*args,**kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self,*args): pass
            async def post(self,url,headers=None,json=None):
                seen.update(url=url,body=json,authorization=headers.get("Authorization"))
                return httpx.Response(200,request=httpx.Request("POST",url),json={
                  "model":candidate,
                  "choices":[{"message":{"role":"assistant","content":"Kết quả hợp lệ"}}],
                  "usage":{"prompt_tokens":50,"completion_tokens":10,"cost":0}})
        with patch.dict(os.environ,{
              "OPENROUTER_API_KEY":"test-secret",
              "LHM_OPENROUTER_FREE_ONLY":"1",
              "LHM_OPENROUTER_FREE_AUTO_SELECT":"0"}):
            with patch("openrouter_free_catalog.select_free_model",new=AsyncMock(return_value=candidate)):
                with patch.object(gateway.httpx,"AsyncClient",Client):
                    answer=asyncio.run(gateway.call_free_model("director",
                        {"industry":"general","business_context":""},
                        "Một câu trả lời ngắn", "", "openrouter/free"))
        self.assertEqual(seen["body"]["model"],candidate)
        self.assertNotEqual(seen["body"]["model"],"openrouter/free")
        self.assertEqual(answer["model"],candidate)
        self.assertEqual(answer["usage"]["cost"],0)

    def test_no_verified_text_model_stops_without_any_api_call(self):
        with patch.dict(os.environ,{"OPENROUTER_API_KEY":"test-secret",
                "LHM_OPENROUTER_FREE_ONLY":"1","LHM_OPENROUTER_FREE_AUTO_SELECT":"0"}):
            with patch("openrouter_free_catalog.select_free_model",new=AsyncMock(side_effect=ValueError("none"))):
                with patch.object(gateway.httpx,"AsyncClient",side_effect=AssertionError("network forbidden")):
                    with self.assertRaises(HTTPException) as exc:
                        asyncio.run(gateway.call_free_model("content",
                          {"industry":"general","business_context":""},
                          "Viết nội dung giả định", "", "openrouter/free"))
        self.assertEqual(exc.exception.status_code,503)

    def test_reasoning_only_length_is_safe_failure(self):
        payload={"model":"nvidia/nemotron-3-ultra-550b-a55b:free",
                "choices":[{"message":{"content":"","reasoning":"PRIVATE"}, "finish_reason":"length"}],
                "usage":{"prompt_tokens":50,"completion_tokens":1100,"cost":0}}
        with patch.dict(os.environ,{"LHM_OPENROUTER_FREE_ONLY":"1"}):
            with self.assertRaises(HTTPException) as exc:
                gateway.validate_free_chat_reply(payload,"openrouter/free")
        self.assertEqual(exc.exception.status_code,502)
        self.assertIn("token",exc.exception.detail)
        self.assertNotIn("PRIVATE",exc.exception.detail)

if __name__=="__main__":
    unittest.main()
