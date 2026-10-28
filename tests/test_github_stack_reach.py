"""Offline regression tests: GitHub tool stack and restricted Agent-Reach execution."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from fastapi import HTTPException

import app
import core
import agent_reach_bridge as reach
import computer_agent
import computer_executor as executor
import github_tool_stack
from agent_runtime_pilot import PILOT_TOOLS


class GithubStackTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(app.app)
        self.runner=TestClient(executor.runner)
        self.temp=tempfile.TemporaryDirectory(prefix="reach_runner_test_")
        self.addCleanup(self.temp.cleanup)
        original=executor.ROOT
        executor.ROOT=Path(self.temp.name)/"sandbox"
        self.addCleanup(setattr,executor,"ROOT",original)

    def test_19_real_repo_entries_and_all_16_roles(self):
        data=self.client.get("/agents/github-stack").json()
        self.assertEqual(data["repo_count"],19)
        self.assertEqual(data["role_count"],16)
        self.assertEqual(len(data["roles"]),16)
        self.assertEqual({x["role"] for x in data["roles"]},set(core.ROLES))
        self.assertEqual(len({x["id"] for x in data["tools"]}),19)
        self.assertFalse(data["provider_connections_verified"])
        self.assertFalse(data["auto_install_enabled"])
        for item in data["tools"]:
            self.assertTrue(item["url"].startswith("https://github.com/"))
            self.assertIn(item["status"],{
                "pilot_adapter","existing_adapter","existing_runtime",
                "selected_not_connected","catalog_reference","research_only","reference_only"})
            self.assertTrue(item["roles"])
        self.assertEqual(self.client.get("/agents/github-stack/ui").status_code,200)
        self.assertIn("Agent-Reach",self.client.get("/agents/github-stack/ui").text)

    def test_missing_role_allowlist_never_inherits_global_browser_hosts(self):
        from fastapi.testclient import TestClient
        with patch.dict(os.environ,{
            "LHM_COMPUTER_ALLOWED_HOSTS":"example.com",
            "LHM_COMPUTER_CONTENT_ALLOWED_HOSTS":"",
            "LHM_COMPUTER_SALES_ALLOWED_HOSTS":"",
            "LHM_COMPUTER_QUALITY_ALLOWED_HOSTS":"",
        }):
            for role in ("content","sales","quality"):
                self.assertFalse(executor._allowed_domain("https://example.com/a",role))

    def test_readonly_work_order_can_start_without_browser_whitelist(self):
        import agent_runtime_pilot as pilot
        import computer_agent
        import agent_work_orders
        tmp=tempfile.TemporaryDirectory(prefix="reach_no_browser_hosts_")
        self.addCleanup(tmp.cleanup)
        original=core.DATABASE
        core.DATABASE=Path(tmp.name)/"test.sqlite3"
        self.addCleanup(setattr,core,"DATABASE",original)
        core.setup()
        agent_work_orders.prepare_db()
        computer_agent.prepare_db()
        pilot.prepare_db()
        with patch.dict(os.environ,{
            "LHM_ADMIN_TOKEN":"",
            "LHM_TOOLS_WORKSPACE":"demo",
            "LHM_COMPUTER_CONTENT_ALLOWED_HOSTS":"",
            "LHM_COMPUTER_ALLOWED_HOSTS":"example.com"
        }):
            order=self.client.post("/workspaces/demo/agents/content/work-orders",json={
                "objective":"Nghiên cứu nguồn công khai cho bài viết marketing mới",
                "idempotency_key":"reach-no-browser-123456"})
            self.assertEqual(order.status_code,201,order.text)
            path="/workspaces/demo/agent-work-orders/"+order.json()["id"]+"/computer-pilot"
            run=self.client.post(path,json={"max_steps":8})
            self.assertEqual(run.status_code,201,run.text)
            pilot.assert_pilot_action(run.json(),"agent_reach_search",
                    {"channel":"github","query":"agent tools"})
            with self.assertRaises(HTTPException):
                pilot.assert_pilot_action(run.json(),"browser_open",
                    {"url":"https://example.com/profile"})

    def test_readonly_agent_reach_visible_only_to_authorized_agent_profiles(self):
        self.assertIn("agent_reach_search",computer_agent.TOOLS)
        self.assertIn("agent_reach_search",PILOT_TOOLS["content"])
        self.assertIn("agent_reach_search",PILOT_TOOLS["quality"])
        self.assertNotIn("agent_reach_search",PILOT_TOOLS["sales"])
        self.assertIn("- agent_reach_search:",computer_agent._system_prompt("research"))
        self.assertNotIn("- agent_reach_search:",computer_agent._system_prompt("sales"))
        self.assertIn("agent_reach_search",executor.ALLOWED_TOOLS)

    def test_invalid_roles_channels_or_url_injection_denied(self):
        for role in ("sales","finance","director","hr"):
            with self.assertRaises(HTTPException) as ctx:
                reach.validate(role,{"channel":"github","query":"agent research"})
            self.assertEqual(ctx.exception.status_code,403)
        for channel in ("facebook","instagram","xiaohongshu","web","shell","youtube;rm"):
            with self.assertRaises(HTTPException):
                reach.validate("research",{"channel":channel,"query":"agent research"})
        for query in ("--help","https://internal.localhost/secret","x\n--install","a"):
            with self.assertRaises(HTTPException):
                reach.validate("content",{"channel":"github","query":query})
        channel,query=reach.validate("content",{"channel":"github","query":"  useful agents  "})
        self.assertEqual((channel,query),("github","useful agents"))

    def test_runner_auth_and_denied_role_cannot_spawn(self):
        req={"workspace_id":"demo","role":"research","tool":"agent_reach_search",
             "args":{"channel":"github","query":"agent runtime"}}
        with patch.dict(os.environ,{"LHM_COMPUTER_RUNNER_TOKEN":"test-runner-only"}):
            self.assertEqual(self.runner.post("/v1/tool",json=req).status_code,401)
            token={"Authorization":"Bearer test-runner-only"}
            with patch.object(reach.shutil,"which",return_value=None):
                missing=self.runner.post("/v1/tool",json=req,headers=token)
            self.assertEqual(missing.status_code,503,missing.text)
            forged={**req,"role":"sales"}
            with patch.object(reach.shutil,"which",return_value="/usr/local/bin/agent-reach") as which:
                deny=self.runner.post("/v1/tool",json=forged,headers=token)
                self.assertEqual(deny.status_code,403,deny.text)
                which.assert_not_called()


class AgentReachSubprocessTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_readonly_argv_and_scrubbed_credentials(self):
        class Fake:
            returncode=0
            async def communicate(self):
                return json.dumps({"schema_version":1,"ok":True,"channel":"github","action":"search","backend":"github-api","items":[{"title":"Public repository","url":"https://github.com/example/repo","text":"public","raw":{"untrusted_secret":"redacted"}}]}).encode(),b""
        captured=[]
        async def launch(*argv,**kw):
            captured.append((argv,kw))
            return Fake()
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ,{"LHM_COMPUTER_RUNNER_TOKEN":"NEVER_PASSED",
                "OPENROUTER_API_KEY":"NEVER_PASSED_TO_SUBPROCESS"}):
                with patch.object(reach.shutil,"which",return_value="/usr/local/bin/agent-reach"):
                    with patch.object(reach.asyncio,"create_subprocess_exec",side_effect=launch):
                        result=await reach.search("demo","content",{
                            "channel":"github","query":"best agent"},Path(tmp))
        self.assertEqual(result["provider"],"Agent-Reach")
        self.assertEqual(result["mode"],"read_only_search")
        self.assertFalse(result["verified"])
        self.assertEqual(result["items"][0]["title"],"Public repository")
        self.assertNotIn("raw",result["items"][0])
        self.assertEqual(result["source_backend"],"github-api")
        argv,kwargs=captured[0]
        self.assertEqual(argv,("/usr/local/bin/agent-reach","github","search","best agent"))
        self.assertFalse(kwargs.get("shell",False))
        self.assertNotIn("OPENROUTER_API_KEY",kwargs["env"])
        self.assertNotIn("LHM_COMPUTER_RUNNER_TOKEN",kwargs["env"])
        self.assertIn("reach-home",kwargs["env"]["HOME"])
        self.assertTrue(kwargs["cwd"].startswith("/"))

    async def test_wrong_search_schema_is_rejected(self):
        class Fake:
            returncode=0
            async def communicate(self):
                return json.dumps({"ok":True,"result":[{"title":"should not pass"}]}).encode(),b""
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(reach.shutil,"which",return_value="/usr/local/bin/agent-reach"):
                with patch.object(reach.asyncio,"create_subprocess_exec",return_value=Fake()):
                    with self.assertRaises(HTTPException) as caught:
                        await reach.search("demo","research",{"channel":"github","query":"agent tool"},Path(tmp))
        self.assertEqual(caught.exception.status_code,502)

    async def test_large_untrusted_payload_is_bounded_to_valid_json(self):
        class Fake:
            returncode=0
            async def communicate(self):
                item={"title":"Result","url":"https://github.com/user/repo",
                      "text":"A"*50000,"raw":{"private":"B"*500000}}
                data={"schema_version":1,"ok":True,"channel":"github",
                      "action":"search","backend":"github-api","items":[item]*5}
                return json.dumps(data).encode(),b""
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(reach.shutil,"which",return_value="/usr/bin/agent-reach"):
                with patch.object(reach.asyncio,"create_subprocess_exec",return_value=Fake()):
                    with self.assertRaises(HTTPException) as bad:
                        await reach.search("demo","research",{
                            "channel":"github","query":"large input"},Path(tmp))
        # Reject excessive untrusted output without storing broken JSON in SQLite.
        self.assertEqual(bad.exception.status_code,502)

    async def test_false_ok_and_invalid_json_do_not_return_unverified_success(self):
        class Fake:
            returncode=0
            def __init__(self,data):self.data=data
            async def communicate(self):return self.data,b""
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(reach.shutil,"which",return_value="/usr/local/bin/agent-reach"):
                for raw in (b'{"ok":false}',b'not-json',b'["not an object"]'):
                    with patch.object(reach.asyncio,"create_subprocess_exec",return_value=Fake(raw)):
                        with self.assertRaises(HTTPException):
                            await reach.search("demo","research",{
                                "channel":"youtube","query":"science videos"},Path(tmp))


if __name__=="__main__":
    unittest.main()
