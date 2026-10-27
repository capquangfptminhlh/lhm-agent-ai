"""Offline tests for real specialist tool adapters; no network or GPU required."""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

import app
import core
import tool_hub
import agent_tool_providers as p

EMAIL={"first_name":"Ngoc","last_name":"Tran","email":"ngoc@example.com","data_origin_confirmed":True}
CONTACT={"name":"Khách hàng thử nghiệm","email":"khach@example.com","data_origin_confirmed":True}
IMAGE={"prompt":"Create a product photo against a light background","negative_prompt":"blur",
       "width":512,"height":512,"steps":12,"seed":42}
RESEARCH={"url":"https://example.com/article"}


class SpecialistToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="lhm_specialist_")
        self.old_db=core.DATABASE
        core.DATABASE=Path(self.tmp.name)/"specialist.sqlite3"
        core.setup()
        tool_hub.prepare_db()
        self.env=patch.dict(os.environ,{
            "LHM_ADMIN_TOKEN":"","LHM_TOOLS_WORKSPACE":"demo",
            "LHM_CRAWL_ALLOWED_HOSTS":"example.com,news.example.com",
            "LHM_CRAWL4AI_MODE":"selfhost",
            "LHM_CRAWL4AI_URL":"http://127.0.0.1:11235",
            "LHM_CRAWL4AI_TOKEN":"crawl-test-secret",
            "LHM_TWENTY_URL":"https://api.twenty.com",
            "LHM_TWENTY_API_KEY":"twenty-test-secret",
            "LHM_CHATWOOT_URL":"https://chatwoot.example.net",
            "LHM_CHATWOOT_API_TOKEN":"chatwoot-test-secret",
            "LHM_CHATWOOT_ACCOUNT_ID":"6",
            "LHM_COMFYUI_URL":"http://127.0.0.1:8188",
            "LHM_COMFYUI_CHECKPOINT":"sd15.safetensors",
        })
        self.env.start()
        self.client=TestClient(app.app)

    def tearDown(self):
        self.env.stop()
        core.DATABASE=self.old_db
        self.tmp.cleanup()

    def create(self,action,payload,key="unit-test-fixed-key"):
        return self.client.post("/workspaces/demo/tool-jobs",json={
            "action":action,"payload":payload,"idempotency_key":key
        })

    def test_tool_catalog_and_readiness(self):
        c=self.client.get("/tools/catalog")
        self.assertEqual(c.status_code,200)
        a=c.json()["actions"]
        for action in p.ACTIONS:
            self.assertIn(action,a)
        readiness=self.client.get("/tools/specialists/readiness")
        self.assertEqual(readiness.status_code,200)
        self.assertTrue(all(x["configured"] for x in readiness.json()["providers"]))
        self.assertTrue(all(x["connection_verified"] is False for x in readiness.json()["providers"]))
        self.assertNotIn("test-secret",str(readiness.json()))
        self.assertIn("toolForm",self.client.get("/agent-tools").text)

    def test_url_allowlist_blocks_private_hosts_ip_redirect_and_credentials(self):
        for value in [
            "https://127.0.0.1/private","http://example.com/article",
            "https://example.com:8443/a","https://news.example.com.evil.io/",
            "https://attacker.example.com/","https://user:pass@example.com/a",
            "https://localhost:443/","https://169.254.169.254/latest/meta-data",
            "file:///etc/passwd","https://example.com@evil.com/"
        ]:
            self.assertEqual(self.create("crawl4ai.crawl",{"url":value}).status_code,422,value)
        self.assertEqual(self.create("crawl4ai.crawl",RESEARCH).status_code,201)
        self.assertTrue(p._url_allowed("https://news.example.com/article"))

    def test_person_and_contact_require_explicit_origin_confirmed(self):
        for action,valid in (("twenty.create_person",EMAIL),("chatwoot.create_contact",CONTACT)):
            incorrect={**valid,"data_origin_confirmed":False}
            self.assertEqual(self.create(action,incorrect,action+"-no-consent").status_code,422)
            allowed=self.create(action,valid,action+"-approved")
            self.assertEqual(allowed.status_code,201,allowed.text)
            self.assertEqual(allowed.json()["status"],"awaiting_approval")

    def test_gpu_request_limits_and_no_arbitrary_graph(self):
        self.assertEqual(self.create("comfyui.generate_image",{**IMAGE,"width":4096}).status_code,422)
        self.assertEqual(self.create("comfyui.generate_image",{**IMAGE,"steps":250}).status_code,422)
        self.assertEqual(self.create("comfyui.generate_image",{**IMAGE,"prompt":""}).status_code,422)
        allowed=self.create("comfyui.generate_image",{**IMAGE,"workflow":{"999":{"class_type":"PythonEval"}}})
        self.assertEqual(allowed.status_code,201,allowed.text)
        graph=p._comfyui_graph(IMAGE)
        self.assertEqual(len(graph),7)
        self.assertEqual({x["class_type"] for x in graph.values()},
          {"CheckpointLoaderSimple","CLIPTextEncode","EmptyLatentImage",
           "KSampler","VAEDecode","SaveImage"})
        self.assertTrue(all("PythonEval" not in str(x) for x in graph.values()))

    def test_exactly_once_approval_before_external_contact_write(self):
        first=self.create("twenty.create_person",EMAIL)
        self.assertEqual(first.status_code,201,first.text)
        job_id=first.json()["id"]
        again=self.create("twenty.create_person",EMAIL)
        self.assertEqual(again.status_code,201)
        self.assertTrue(again.json()["deduplicated"])
        self.assertEqual(again.json()["id"],job_id)
        post=AsyncMock(return_value={"data":{"id":"person-2026"}})
        with patch.object(p,"_post",post):
            # No tool should execute without human approval.
            pending=self.client.post(f"/tool-jobs/{job_id}/execute")
            self.assertEqual(pending.status_code,409)
            post.assert_not_awaited()
            approved=self.client.post(f"/tool-jobs/{job_id}/approve")
            self.assertEqual(approved.status_code,200)
            reply=self.client.post(f"/tool-jobs/{job_id}/execute")
            self.assertEqual(reply.status_code,200,reply.text)
            self.assertEqual(reply.json()["status"],"accepted_unverified")
            self.assertEqual(reply.json()["receipt"]["provider_record_id"],"person-2026")
            self.assertNotIn("ngoc@example.com",str(reply.json()))
            duplicate=self.client.post(f"/tool-jobs/{job_id}/execute")
            self.assertEqual(duplicate.status_code,409)
            self.assertEqual(post.await_count,1)
        called=post.call_args
        self.assertEqual(called.args[0],"https://api.twenty.com/rest/people")
        self.assertEqual(called.args[1]["name"]["firstName"],"Ngoc")
        self.assertEqual(called.args[2]["Authorization"],"Bearer twenty-test-secret")

    def test_chatwoot_payload_and_receipt_do_not_expose_sensitive_data(self):
        result=AsyncMock(return_value={"payload":{"id":432,"name":"private","email":"secret@example.com"}})
        with patch.object(p,"_post",result):
            value=asyncio.run(p.dispatch("chatwoot.create_contact",CONTACT))
        self.assertEqual(value["provider_record_id"],"432")
        self.assertNotIn("email",value)
        request=result.call_args
        self.assertEqual(request.args[0],"https://chatwoot.example.net/api/v1/accounts/6/contacts")
        self.assertEqual(request.args[2]["api_access_token"],"chatwoot-test-secret")

    def test_research_crawl4ai_receipt_has_source(self):
        answer=AsyncMock(return_value={"success":True,"results":[{
            "success":True,"url":"https://example.com/article",
            "markdown":"# Public company profile\nOpening hours: 9-18"
        }]})
        with patch.object(p,"_post",answer):
            got=asyncio.run(p.dispatch("crawl4ai.crawl",RESEARCH))
        self.assertEqual(got["provider"],"crawl4ai")
        self.assertEqual(got["source_url"],RESEARCH["url"])
        self.assertIn("Public company",got["markdown_excerpt"])
        self.assertEqual(answer.call_args.args[0],"http://127.0.0.1:11235/crawl")
        self.assertEqual(answer.call_args.args[1],{"urls":[RESEARCH["url"]]})
        with patch.dict(os.environ,{"LHM_CRAWL4AI_MODE":"cloud"}):
            answer.reset_mock(return_value=True)
            answer.return_value={"markdown":"Cloud text"}
            with patch.object(p,"_post",answer):
                cloud=asyncio.run(p.dispatch("crawl4ai.crawl",RESEARCH))
            self.assertEqual(cloud["provider"],"crawl4ai")
            self.assertEqual(answer.call_args.args[0],"https://api.crawl4ai.com/scrape")

    def test_comfyui_enqueue_requires_provider_prompt_id(self):
        valid_id="7c915bb0-8e2e-40be-89db-bbafc1b66166"
        request=AsyncMock(return_value={"prompt_id":valid_id,"number":0,"node_errors":{}})
        with patch.object(p,"_post",request):
            value=asyncio.run(p.dispatch("comfyui.generate_image",IMAGE))
        self.assertEqual(value["prompt_id"],valid_id)
        self.assertEqual(value["confirmation"],"image_queued_not_rendered")
        called=request.call_args
        self.assertEqual(called.args[0],"http://127.0.0.1:8188/prompt")
        self.assertEqual(called.args[1]["prompt"]["1"]["inputs"]["ckpt_name"],"sd15.safetensors")
        with patch.object(p,"_post",AsyncMock(return_value={"queue":"ok"})):
            with self.assertRaises(ValueError):
                asyncio.run(p.dispatch("comfyui.generate_image",IMAGE))

    def test_image_history_requires_owned_receipt(self):
        ident="7c915bb0-8e2e-40be-89db-bbafc1b66166"
        blocked=self.client.get("/tools/comfyui/jobs/"+ident)
        self.assertEqual(blocked.status_code,404)
        job=self.create("comfyui.generate_image",IMAGE)
        self.assertEqual(job.status_code,201)
        jid=job.json()["id"]
        self.assertEqual(self.client.post(f"/tool-jobs/{jid}/approve").status_code,200)
        with patch.object(p,"_post",AsyncMock(return_value={"prompt_id":ident,"node_errors":{}})):
            sent=self.client.post(f"/tool-jobs/{jid}/execute")
        self.assertEqual(sent.status_code,200,sent.text)
        class Client:
            def __init__(self,*args,**kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self,*args): return False
            async def get(self,url):
                return httpx.Response(200,request=httpx.Request("GET",url),json={
                    ident:{"status":{"completed":True},"outputs":{"7":{"images":[{
                     "filename":"LHM_001.png","subfolder":"","type":"output"}]}}}})
        import httpx
        with patch.object(p.httpx,"AsyncClient",Client):
            resp=self.client.get("/tools/comfyui/jobs/"+ident)
        self.assertEqual(resp.status_code,200,resp.text)
        self.assertEqual(resp.json()["status"],"finished")
        self.assertEqual(resp.json()["images"][0]["filename"],"LHM_001.png")

    def test_missing_connection_fails_closed(self):
        with patch.dict(os.environ,{"LHM_TWENTY_API_KEY":""}):
            self.assertFalse(p.configured("twenty"))
            with self.assertRaises(ValueError):
                asyncio.run(p.dispatch("twenty.create_person",EMAIL))
        self.assertEqual(self.create("not_a_real_tool",{"foo":"bar"}).status_code,422)


if __name__ == "__main__":
    unittest.main()
