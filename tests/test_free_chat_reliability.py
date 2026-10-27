"""Free-only response validation and role-aware text-model selection regression."""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi import HTTPException
from openrouter_provider import validate_free_chat_reply
from openrouter_free_catalog import (
    is_text_chat_candidate,fetch_verified_text_models,rank_for_role,select_free_model
)


def valid_reply():
    return {"model":"nvidia/nemotron-3-ultra-550b-a55b:free",
            "choices":[{"message":{"role":"assistant","content":"Kết quả có căn cứ"}}],
            "usage":{"prompt_tokens":30,"completion_tokens":20,"cost":0}}


class FreeChatResponseTests(unittest.TestCase):
    def test_valid_explicitly_free_zero_cost_text_response(self):
        with patch.dict(os.environ,{"LHM_OPENROUTER_FREE_ONLY":"1"}):
            self.assertEqual(
                validate_free_chat_reply(valid_reply(),"openrouter/free")["usage"]["cost"],0)

    def test_text_parts_normalize_to_plain_string(self):
        r=valid_reply()
        r["choices"][0]["message"]["content"]=[
            {"type":"text","text":"Kết "},{"type":"text","text":"quả"}]
        with patch.dict(os.environ,{"LHM_OPENROUTER_FREE_ONLY":"1"}):
            result=validate_free_chat_reply(r,"openrouter/free")
        self.assertEqual(result["choices"][0]["message"]["content"],"Kết quả")

    def test_reasoning_only_empty_content_fails_closed(self):
        for malformed in (None,"",[],{"text":"not-supported"}):
            r=valid_reply()
            r["choices"][0]["message"]["content"]=malformed
            r["choices"][0]["message"]["reasoning"]="Private reasoning only"
            with self.subTest(malformed=repr(malformed)):
                with self.assertRaises(HTTPException) as err:
                    validate_free_chat_reply(r,"openrouter/free")
                self.assertEqual(err.exception.status_code,502)
                self.assertNotIn("Private reasoning",str(err.exception.detail))

    def test_provider_disallows_malformed_and_missing_usage(self):
        for usage in (None,{},{"prompt_tokens":10},
                      {"prompt_tokens":True,"completion_tokens":1},
                      {"prompt_tokens":-1,"completion_tokens":10},
                      {"prompt_tokens":3.5,"completion_tokens":1}):
            r=valid_reply()
            r["usage"]=usage
            with self.subTest(usage=usage):
                with self.assertRaises(HTTPException):
                    validate_free_chat_reply(r,"openrouter/free")

    def test_cost_nonzero_missing_nan_negative_and_boolean_never_pass(self):
        for cost in (0.00001,-1,"NaN","Infinity",True,False,"abc"):
            r=valid_reply()
            r["usage"]["cost"]=cost
            with self.subTest(cost=cost):
                with self.assertRaises(HTTPException):
                    validate_free_chat_reply(r,"openrouter/free")
        r=valid_reply();r["usage"].pop("cost")
        with patch.dict(os.environ,{"LHM_OPENROUTER_FREE_ONLY":"1"}):
            with self.assertRaises(HTTPException) as err:
                validate_free_chat_reply(r,"openrouter/free")
            self.assertIn("chi phí",err.exception.detail)

    def test_paid_actual_model_cannot_be_accepted_from_free_router(self):
        for actual in ("openai/gpt-4", "openrouter/auto", "openrouter/free",
                       "google/gemma-4-31b", 2):
            r=valid_reply();r["model"]=actual
            with self.subTest(actual=actual):
                with self.assertRaises(HTTPException):
                    validate_free_chat_reply(r,"openrouter/free")
        r=valid_reply()
        with self.assertRaises(HTTPException):
            validate_free_chat_reply(r,"google/gemma-4-31b:free")

    def test_malformed_choices_never_get_stored(self):
        for choices in (None,[],[None],[{}],{"message":"raw"}):
            r=valid_reply();r["choices"]=choices
            with self.subTest(choices=choices):
                with self.assertRaises(HTTPException):
                    validate_free_chat_reply(r,"openrouter/free")


class FreeTextSelectionTests(unittest.TestCase):
    def test_require_advertised_text_input_and_text_output(self):
        item={"id":"google/gemma-4-31b:free","name":"Google: Gemma 4 31B (free)",
              "pricing":{"prompt":"0","completion":"0"},
              "architecture":{"input_modalities":["text"],"output_modalities":["text"]}}
        self.assertTrue(is_text_chat_candidate(item))
        bad_arch=[
            {"input_modalities":["image"],"output_modalities":["text"]},
            {"input_modalities":["text"],"output_modalities":["image"]},
            {"input_modalities":["text"]},
            None,
        ]
        for arch in bad_arch:
            with self.subTest(arch=arch):
                self.assertFalse(is_text_chat_candidate({**item,"architecture":arch}))
        self.assertFalse(is_text_chat_candidate({
            **item,"id":"nvidia/nemotron-3.5-content-safety:free",
            "name":"NVIDIA: Nemotron 3.5 Content Safety (free)"}))

    def test_select_only_owner_approved_free_text_model(self):
        catalog={"data":[
            {"id":"nvidia/nemotron-3.5-content-safety:free",
             "name":"NVIDIA: Nemotron 3.5 Content Safety (free)",
             "pricing":{"prompt":"0","completion":"0"},
             "architecture":{"input_modalities":["text"],"output_modalities":["text"]}},
            {"id":"google/gemma-4-31b:free","name":"Google: Gemma 4 31B (free)",
             "pricing":{"prompt":"0","completion":"0"},
             "architecture":{"input_modalities":["text"],"output_modalities":["text"]}},
            {"id":"google/gemma-4-31b","name":"Google: Gemma 4 31B",
             "pricing":{"prompt":"0","completion":"0"},
             "architecture":{"input_modalities":["text"],"output_modalities":["text"]}}
        ]}
        import httpx
        seen={}
        class Client:
            def __init__(self,*args,**kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self,*args): pass
            async def get(self,url,params=None):
                seen.update(url=url,params=params)
                return httpx.Response(200,json=catalog,request=httpx.Request("GET",url))
        with patch("openrouter_free_catalog.httpx.AsyncClient",Client):
            models=asyncio.run(fetch_verified_text_models())
        self.assertEqual(models,["google/gemma-4-31b:free"])
        self.assertEqual(seen["params"],{"output_modalities":"text"})

    def test_no_text_chat_model_never_falls_back_to_unsafe_model(self):
        import httpx
        catalog={"data":[{"id":"nvidia/nemotron-3.5-content-safety:free",
               "name":"NVIDIA: Nemotron 3.5 Content Safety (free)",
               "pricing":{"prompt":"0","completion":"0"},
               "architecture":{"input_modalities":["text"],"output_modalities":["text"]}}]}
        class Client:
            def __init__(self,*args,**kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self,*args): pass
            async def get(self,url,params=None):
                return httpx.Response(200,json=catalog,request=httpx.Request("GET",url))
        with patch("openrouter_free_catalog.httpx.AsyncClient",Client):
            with self.assertRaises(ValueError):
                asyncio.run(select_free_model("quality"))


if __name__=="__main__":
    unittest.main()
