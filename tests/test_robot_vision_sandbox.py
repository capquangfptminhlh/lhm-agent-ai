"""Fail-closed tests of Grok-style screenshot observation and sandbox.

Tests use actual SQLite task/run rows, mock provider transport, no user keys,
no external network, no Docker or hidden Windows background control.
"""
from __future__ import annotations
import asyncio
import base64
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app
import core
import computer_agent as ca
import computer_executor as ce
import robot_sandbox as sb
import robot_vision as rv
import worker

PNG=b"\x89PNG\r\n\x1a\n"+b"valid-png-test-only"
VISION_REPLY={
  "choices":[{"message":{"content":"Cửa sổ có nút Tìm kiếm, trường nhập liệu và nút Lưu."}}],
  "usage":{"prompt_tokens":120,"completion_tokens":36,"cost":0},
  "cost":0,
}

class VisionAndSandboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="lhm_vision_sandbox_")
        self.original=core.DATABASE
        core.DATABASE=Path(self.tmp.name)/"robot.sqlite3"
        core.setup();ca.prepare_db()
        self.patchenv=patch.dict(os.environ,{
            "LHM_ADMIN_TOKEN":"",
            "LHM_DESKTOP_NATIVE":"1",
            "LHM_COMPUTER_RUNNER_TOKEN":"test-runner-not-a-real-token",
            "LHM_COMPUTER_VISION_ENABLED":"0",
            "LHM_COMPUTER_VISION_MODEL":"",
            "OPENROUTER_API_KEY":"",
            "LHM_SANDBOX_ENABLED":"0",
            "LHM_AGENT_PARALLELISM":"1"
        })
        self.patchenv.start()
        self.patcher_root=patch.object(ce,"ROOT",Path(self.tmp.name)/"agent-data")
        self.patcher_root.start()
        self.client=TestClient(app.app)

    def tearDown(self):
        self.patcher_root.stop()
        self.patchenv.stop()
        core.DATABASE=self.original
        self.tmp.cleanup()

    def new_run(self,key):
        response=self.client.post("/workspaces/demo/computer/runs",json={
            "role":"research","objective":"Quan sát ứng dụng và xác định những bước tiếp theo",
            "max_steps":5,"idempotency_key":key
        })
        self.assertEqual(response.status_code,201,response.text)
        return response.json()["id"]

    def test_vision_is_disabled_by_default_and_rejects_paid_models(self):
        self.assertFalse(rv.enabled())
        with self.assertRaises(HTTPException):
            ca._parse_action('{"tool":"vision_observe","args":{"source":"browser"}}')
        with patch.dict(os.environ,{
            "LHM_COMPUTER_VISION_ENABLED":"1",
            "OPENROUTER_API_KEY":"test-only-key",
            "LHM_COMPUTER_VISION_MODEL":"provider/vision-paid"
        }):
            self.assertFalse(rv.enabled())
        with patch.dict(os.environ,{
            "LHM_COMPUTER_VISION_ENABLED":"1",
            "OPENROUTER_API_KEY":"test-only-key",
            "LHM_COMPUTER_VISION_MODEL":"provider/vision:free"
        }):
            self.assertTrue(rv.enabled())
            tool,args=ca._parse_action('{"tool":"vision_observe","args":{"source":"windows","hwnd":42}}')
            self.assertEqual((tool,args["hwnd"]),("vision_observe",42))

    def test_vision_requires_exactly_browser_or_selected_hwnd(self):
        for body in ({"source":"file"},{"source":"browser","hwnd":42},
                     {"source":"windows"},{"source":"windows","hwnd":"42"},
                     {"source":"windows","hwnd":-1},{"source":"windows","hwnd":42,"url":"x"}):
            with self.assertRaises(HTTPException,msg=str(body)):
                rv.validate(body)
        self.assertEqual(rv.validate({"source":"browser"}),("browser_screenshot",{}))
        self.assertEqual(rv.validate({"source":"windows","hwnd":13}),
                         ("windows_screenshot",{"hwnd":13}))

    def test_vision_only_sends_image_after_explicit_step_approval(self):
        rid=self.new_run("robot-vision-consent-1")
        with patch.dict(os.environ,{
            "LHM_COMPUTER_VISION_ENABLED":"1",
            "OPENROUTER_API_KEY":"test-only-key",
            "LHM_COMPUTER_VISION_MODEL":"provider/vision:free"
        }):
            with patch.object(ca,"_next_action",AsyncMock(return_value=(
                    "vision_observe",{"source":"windows","hwnd":123}))):
                with patch.object(rv,"observe",AsyncMock()) as invoke:
                    proposal=self.client.post("/computer/runs/"+rid+"/tick")
                    self.assertEqual(proposal.status_code,200,proposal.text)
                    self.assertEqual(proposal.json()["status"],"awaiting_approval")
                    invoke.assert_not_awaited()
                    sid=proposal.json()["step_id"]
                    approved=self.client.post(f"/computer/runs/{rid}/steps/{sid}/approve")
                    self.assertEqual(approved.status_code,200,approved.text)
                    invoke.return_value={"source":"windows",
                        "observation":"Nút Lưu hiện phía dưới",
                        "verification":"AI_visual_interpretation_not_verified",
                        "screenshot_stored":False}
                    executed=self.client.post("/computer/runs/"+rid+"/tick")
                    self.assertEqual(executed.status_code,200,executed.text)
                    self.assertEqual(executed.json()["last_tool"],"vision_observe")
                    invoke.assert_awaited_once()
        detail=self.client.get("/computer/runs/"+rid).json()
        self.assertFalse(detail["steps"][0]["result"]["screenshot_stored"])
        self.assertNotIn("png_base64",str(detail))

    def test_actual_vision_api_payload_is_bounded_and_redacted(self):
        class Response:
            def raise_for_status(self):pass
            def json(self):return VISION_REPLY
        class Client:
            def __init__(self,*args,**kwargs):
                self.request=None
            async def __aenter__(self):return self
            async def __aexit__(self,*args):return False
            async def post(self,url,**kwargs):
                self.request=kwargs
                self.__class__.last=(url,kwargs)
                return Response()
        with patch.dict(os.environ,{
            "LHM_COMPUTER_VISION_ENABLED":"1",
            "OPENROUTER_API_KEY":"test-only-key",
            "LHM_COMPUTER_VISION_MODEL":"provider/vision:free"
        }):
            with patch.object(ca,"runner_call",AsyncMock(return_value={
                "png_base64":base64.b64encode(PNG).decode(),"url":"https://example.com"})) as capture:
                with patch.object(rv.httpx,"AsyncClient",Client):
                    result=asyncio.run(rv.observe({"workspace_id":"demo","role":"research"},
                                                   {"source":"browser"}))
        capture.assert_awaited_once()
        url,body=Client.last
        self.assertEqual(url,rv.OPENROUTER_URL)
        self.assertEqual(body["json"]["model"],"provider/vision:free")
        image=body["json"]["messages"][1]["content"][1]["image_url"]["url"]
        self.assertTrue(image.startswith("data:image/png;base64,"))
        self.assertEqual(base64.b64decode(image.split(",",1)[1]),PNG)
        self.assertIn("Tìm kiếm",result["observation"])
        self.assertNotIn("png_base64",result)
        self.assertFalse(result["screenshot_stored"])
        self.assertNotIn("test-only-key",str(result))
        with core.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM usage").fetchone()[0],1)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM openrouter_attempts").fetchone()[0],1)

    def test_screenshot_rejects_nonpng_large_or_garbled(self):
        for item in [{"png_base64":""},{"png_base64":"%%%"},
                     {"png_base64":base64.b64encode(b"NOTPNG").decode()},
                     {"png_base64":base64.b64encode(b"\x89PNG\r\n\x1a\n"+b"a"*(rv.MAX_IMAGE_BYTES+1)).decode()}]:
            with self.assertRaises(HTTPException):
                rv._decode_png(item)

    def test_sandbox_disabled_without_operator_opt_in(self):
        self.assertFalse(sb.configured())
        with self.assertRaises(HTTPException):
            ca._parse_action('{"tool":"sandbox_run","args":{"argv":["python","-V"]}}')
        with self.assertRaises(HTTPException):
            asyncio.run(sb.run("demo","research",{"argv":["python","-V"]}))
        self.assertTrue(ca._approval_required("sandbox_run"))

    def test_sandbox_rejects_shell_and_unsupported_inputs(self):
        for data in [{"argv":["bash","-c","id"]},
                     {"argv":["powershell","-Command","x"]},
                     {"argv":["python","-c","x"],"mount":"/"},
                     {"argv":["/bin/python","-V"]},
                     {"argv":[]}]:
            with self.assertRaises(HTTPException):
                sb.validate(data)
        self.assertEqual(sb.validate({"argv":["python","-V"]}),["python","-V"])

    def test_sandbox_passes_no_network_and_no_host_home_or_token(self):
        with patch.dict(os.environ,{"LHM_SANDBOX_ENABLED":"1",
                                    "OPENROUTER_API_KEY":"should-not-be-shared"}):
            with patch.object(sb,"docker_binary",return_value="docker"):
                with patch.object(sb,"_exec",new_callable=AsyncMock,return_value=(
                    0,b"hello sandbox\n",b"")) as execute:
                    result=asyncio.run(sb.run("demo","research",{"argv":["python","-V"]}))
        self.assertEqual(result["exit_code"],0)
        command=execute.call_args.args[0]
        self.assertEqual(command[0:3],["docker","run","--rm"])
        self.assertIn("--network",command)
        self.assertEqual(command[command.index("--network")+1],"none")
        self.assertIn("--pull",command)
        self.assertEqual(command[command.index("--pull")+1],"never")
        self.assertIn("--cap-drop",command)
        self.assertIn("--read-only",command)
        self.assertNotIn("should-not-be-shared",str(command))
        self.assertNotIn("robot-settings.json",str(command))
        self.assertEqual(command[-3:],["python:3.12-slim","python","-V"])
        self.assertIn("/demo/research/files",command[command.index("--mount")+1].replace("\\","/"))
        self.assertEqual(result["isolation"],"docker_no_network_not_vm")

    def test_sandbox_timeout_force_removes_named_container(self):
        calls=[]
        async def fake(argv,timeout=25):
            calls.append(argv)
            if len(calls)==1:raise asyncio.TimeoutError()
            return 0,b"",b""
        with patch.dict(os.environ,{"LHM_SANDBOX_ENABLED":"1"}):
            with patch.object(sb,"docker_binary",return_value="docker"):
                with patch.object(sb,"_exec",side_effect=fake):
                    result=asyncio.run(sb.run("demo","research",{"argv":["python","-V"]}))
        self.assertTrue(result["timed_out"])
        self.assertEqual(calls[1][:3],["docker","rm","--force"])
        self.assertEqual(calls[1][3],calls[0][calls[0].index("--name")+1])

    def test_parallel_coworkers_and_same_role_sequential(self):
        async def check():
            active=0;highest=0;seen=[]
            async def execute(task_id):
                nonlocal active,highest
                active+=1;highest=max(active,highest);seen.append(task_id)
                await asyncio.sleep(.015)
                active-=1
            jobs=[
                {"id":"one","workspace_id":"demo","role":"research","model":"deepseek-flash"},
                {"id":"conflict","workspace_id":"demo","role":"research","model":"deepseek-flash"},
                {"id":"two","workspace_id":"demo","role":"content","model":"deepseek-flash"},
                {"id":"three","workspace_id":"demo","role":"quality","model":"deepseek-flash"}
            ]
            with patch.dict(os.environ,{"LHM_AGENT_PARALLELISM":"2"}):
                with patch.object(worker,"execute_task",side_effect=execute):
                    with patch.object(worker,"model_configured",return_value=True):
                        count=await worker.dispatch_agent_batch(jobs)
            return count,seen,highest
        count,seen,highest=asyncio.run(check())
        self.assertEqual(count,2)
        self.assertEqual(seen,["one","two"])
        self.assertEqual(highest,2)

    def test_parallelism_cap_never_exceeds_four(self):
        with patch.dict(os.environ,{"LHM_AGENT_PARALLELISM":"999"}):
            self.assertEqual(worker.agent_parallelism(),4)
        with patch.dict(os.environ,{"LHM_AGENT_PARALLELISM":"oops"}):
            self.assertEqual(worker.agent_parallelism(),1)
        with patch.dict(os.environ,{"LHM_AGENT_PARALLELISM":"0"}):
            self.assertEqual(worker.agent_parallelism(),1)


if __name__=="__main__":
    unittest.main()
