"""Offline E2E gates for Work Order → shared Computer → Postiz → deterministic read-back."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch

from fastapi.testclient import TestClient

import app
import core
import agent_work_orders as orders
import computer_agent as computer
import computer_executor as executor
import agent_runtime_pilot as pilot
import tool_hub
import postiz_verifier
from publication_gate import record_review


class SharedAgentPilotTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="lhm_pilot_")
        self.addCleanup(self.tmp.cleanup)
        previous=core.DATABASE
        core.DATABASE=Path(self.tmp.name)/"test.sqlite3"
        self.addCleanup(setattr,core,"DATABASE",previous)
        core.setup()
        orders.prepare_db()
        computer.prepare_db()
        tool_hub.prepare_db()
        pilot.prepare_db()
        self.env=patch.dict(os.environ,{
            "LHM_ADMIN_TOKEN":"","LHM_TOOLS_WORKSPACE":"demo",
            "LHM_OPENROUTER_FREE_ONLY":"1","OPENROUTER_API_KEY":"offline-test-only",
            "LHM_POSTIZ_API_KEY":"mock-postiz-key",
            "LHM_CONTENT_POSTIZ_ALLOWED_CHANNEL_IDS":"channel-1,channel-2",
            "LHM_COMPUTER_ALLOWED_HOSTS":"content.example.com,crm.example.com,example.com",
            "LHM_COMPUTER_CONTENT_ALLOWED_HOSTS":"content.example.com,example.com",
            "LHM_COMPUTER_SALES_ALLOWED_HOSTS":"crm.example.com",
            "LHM_COMPUTER_QUALITY_ALLOWED_HOSTS":"content.example.com,crm.example.com",
        })
        self.env.start();self.addCleanup(self.env.stop)
        self.client=TestClient(app.app)

    def create_order(self,role="content",key=None):
        r=self.client.post("/workspaces/demo/agents/"+role+"/work-orders",json={
            "objective":"Kiểm tra và xử lý một công việc thật theo dữ liệu đã được cung cấp",
            "idempotency_key":key or "pilot-order-"+role+"-12345"})
        self.assertEqual(r.status_code,201,r.text)
        return r.json()

    def finish_and_pass_qa(self,order):
        content="Nội dung demo chính xác, đang chờ kiểm tra thật."
        with core.db() as c:
            c.execute("UPDATE tasks SET status='completed',result=? WHERE id=?",
                      (content,order["task_id"]))
        qa=self.client.post("/workspaces/demo/agent-work-orders/"+order["id"]+"/quality-review")
        self.assertEqual(qa.status_code,201,qa.text)
        review='{"verdict":"pass","reason":"Bản nháp thử nghiệm"}'
        with core.db() as c:
            c.execute("UPDATE tasks SET status='completed',result=? WHERE id=?",
                      (review,qa.json()["qa_task_id"]))
            record_review(c,{"id":qa.json()["qa_task_id"],"role":"quality",
                             "workspace_id":"demo","depends_on":order["task_id"]},review)
        return content

    def handoff(self,order,channels=None,key="test-social-123456",mode="now"):
        return self.client.post("/workspaces/demo/agent-work-orders/"+order["id"]+"/social-handoff",
            json={"channel_ids":channels or ["channel-1"],"mode":mode,
                  "idempotency_key":key})

    def test_three_profiles_have_disjoint_tool_scopes_and_no_verified_claim(self):
        r=self.client.get("/agents/runtime-pilot")
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual({x["role"] for x in r.json()["roles"]},{"content","sales","quality"})
        self.assertFalse(r.json()["tenant_isolation_proven"])
        self.assertFalse(r.json()["verified_live"])
        profiles={x["role"]:x for x in r.json()["roles"]}
        self.assertIn("browser_click",profiles["content"]["tools"])
        self.assertNotIn("browser_click",profiles["sales"]["tools"])
        self.assertNotIn("write_file",profiles["content"]["tools"])
        self.assertTrue(all(x["memory_scope"]=="workspace/role" for x in profiles.values()))
        self.assertNotIn("mock-postiz-key",r.text)

    def test_computer_work_order_binding_and_real_rbac_on_executor(self):
        order=self.create_order()
        url="/workspaces/demo/agent-work-orders/"+order["id"]+"/computer-pilot"
        start=self.client.post(url,json={"max_steps":6})
        self.assertEqual(start.status_code,201,start.text)
        run=start.json()
        self.assertEqual(run["role"],"content")
        self.assertEqual(run["work_order_id"],order["id"])
        self.assertEqual(self.client.post(url,json={"max_steps":6}).json()["id"],run["id"])
        self.assertEqual(pilot.pilot_role_for_run(run["id"]),"content")
        prompt=computer._system_prompt("content",run["id"])
        self.assertIn("browser_click",prompt)
        self.assertNotIn("- run_command:",prompt)
        self.assertNotIn("- windows_click:",prompt)
        with self.assertRaises(Exception):
            pilot.assert_pilot_action(run,"run_command",{"argv":["python","-c","print(1)"]})
        with self.assertRaises(Exception):
            pilot.assert_pilot_action(run,"browser_open",{"url":"https://crm.example.com/private"})
        pilot.assert_pilot_action(run,"browser_open",{"url":"https://content.example.com/demo"})
        # Runtime must reject even when model is tricked into choosing a denied tool.
        with patch.object(computer,"_next_action",new=AsyncMock(return_value=(
            "write_file",{"path":"private.txt","content":"leak"}))):
            with patch.object(computer,"runner_call",new=AsyncMock()) as runner:
                actual=self.client.post("/computer/runs/"+run["id"]+"/tick")
                self.assertEqual(actual.status_code,200,actual.text)
                self.assertEqual(actual.json()["status"],"needs_review")
                runner.assert_not_awaited()

    def test_browser_host_fence_enforced_in_sidecar(self):
        self.assertTrue(executor._allowed_domain("https://content.example.com/","content"))
        self.assertFalse(executor._allowed_domain("https://crm.example.com/","content"))
        self.assertTrue(executor._allowed_domain("https://crm.example.com/","sales"))
        self.assertFalse(executor._allowed_domain("https://content.example.com/","sales"))
        self.assertFalse(executor._allowed_domain("https://127.0.0.1/admin","content"))
        self.assertFalse(executor._allowed_domain("http://content.example.com/","content"))

    def test_social_handoff_is_content_only_whitelisted_and_QA_passed(self):
        content=self.create_order()
        self.assertEqual(self.handoff(content).status_code,422)  # source AI task not completed
        self.finish_and_pass_qa(content)
        self.assertEqual(self.handoff(content,["not-approved"]).status_code,403)
        self.assertEqual(self.handoff(content,["channel-1","channel-1"]).status_code,403)
        sales=self.create_order("sales")
        self.finish_and_pass_qa(sales)
        self.assertEqual(self.handoff(sales,key="sales-publish-attempt").status_code,404)
        first=self.handoff(content)
        self.assertEqual(first.status_code,201,first.text)
        result=first.json()
        self.assertEqual(result["status"],"awaiting_approval")
        self.assertFalse(result["provider_published_verified"])
        duplicate=self.handoff(content)
        self.assertEqual(duplicate.json()["id"],result["id"])
        self.assertTrue(duplicate.json()["deduplicated"])
        self.assertEqual(self.client.post("/tool-jobs/"+result["id"]+"/execute").status_code,409)
        state=self.client.get("/workspaces/demo/agent-work-orders/"+content["id"]+"/execution")
        self.assertEqual(state.json()["stage"],"needs_human")
        self.assertFalse(state.json()["verified_external_completion"])

    def test_provider_postiz_verified_only_after_authenticated_readback(self):
        order=self.create_order()
        self.finish_and_pass_qa(order)
        job=self.handoff(order).json()
        self.assertEqual(self.client.post("/tool-jobs/"+job["id"]+"/approve").status_code,200)
        seen=[]
        async def mocked(method,endpoint,data=None):
            seen.append((method,endpoint))
            if (method,endpoint)==("GET","/integrations"):
                return [{"id":"channel-1","identifier":"facebook","disabled":False}]
            if (method,endpoint)==("POST","/posts"):
                self.assertEqual(data["posts"][0]["integration"]["id"],"channel-1")
                return [{"postId":"post-100","integration":"channel-1"}]
            if (method,endpoint)==("GET","/posts"):
                return {"posts":[{"id":"post-100","state":"PUBLISHED",
                   "integration":{"id":"channel-1"},
                   "releaseURL":"https://www.facebook.com/example/posts/100"}]}
            self.fail("Unexpected Postiz request: "+str((method,endpoint)))
        with patch.object(tool_hub,"_postiz_request",new=mocked):
            sent=self.client.post("/tool-jobs/"+job["id"]+"/execute")
            self.assertEqual(sent.status_code,200,sent.text)
            self.assertEqual(sent.json()["status"],"accepted_unverified")
            before=self.client.get("/workspaces/demo/agent-work-orders/"+order["id"]+"/execution").json()
            self.assertEqual(before["stage"],"verifying")
            self.assertFalse(before["verified_external_completion"])
            verified=self.client.post("/tool-jobs/"+job["id"]+"/verify")
            self.assertEqual(verified.status_code,200,verified.text)
            self.assertEqual(verified.json()["status"],"verified_published")
            self.assertEqual(verified.json()["verification"]["method"],"postiz_authenticated_readback")
            self.assertEqual(verified.json()["verification"]["posts"][0]["release_url"],
                             "https://www.facebook.com/example/posts/100")
        self.assertEqual(seen.count(("POST","/posts")),1)
        self.assertEqual(self.client.post("/tool-jobs/"+job["id"]+"/execute").status_code,409)
        done=self.client.get("/workspaces/demo/agent-work-orders/"+order["id"]+"/execution").json()
        self.assertEqual(done["stage"],"completed")
        self.assertTrue(done["verified_external_completion"])

    def test_postiz_draft_and_partial_provider_receipts_cannot_pass(self):
        import asyncio
        data={"provider_receipt":[{"postId":"post-1","integration":"channel-1"},
                                  {"postId":"post-2","integration":"channel-2"}]}
        async def partial(method,endpoint):
            return {"posts":[{"id":"post-1","integration":{"id":"channel-1"},
                              "state":"PUBLISHED","releaseURL":"https://social.example.com/a"},
                             {"id":"post-2","integration":{"id":"channel-2"},
                              "state":"DRAFT","releaseURL":None}]}
        v=asyncio.run(postiz_verifier.verify_postiz_receipt(data,partial))
        self.assertFalse(v["verified"])
        self.assertEqual(len(v["posts"]),2)
        async def spoofed(method,endpoint):
            return {"posts":[{"id":"post-1","integration":{"id":"channel-1"},
                              "state":"PUBLISHED","releaseURL":"https://127.0.0.1/private"},
                             {"id":"post-2","integration":{"id":"channel-2"},
                              "state":"PUBLISHED","releaseURL":"https://social.example.com/b"}]}
        v=asyncio.run(postiz_verifier.verify_postiz_receipt(data,spoofed))
        self.assertFalse(v["verified"])

    def test_no_sales_or_unlisted_channel_can_publish_via_generic_toolhub_api(self):
        sales=self.create_order("sales")
        self.finish_and_pass_qa(sales)
        direct=self.client.post("/workspaces/demo/tool-jobs",json={
            "action":"postiz.create_post",
            "payload":{"mode":"draft","content":"Nội dung demo chính xác, đang chờ kiểm tra thật.",
                       "channel_ids":["channel-1"],"source_task_id":sales["task_id"]},
            "idempotency_key":"impersonated-sales-postiz"})
        self.assertEqual(direct.status_code,403,direct.text)
        content=self.create_order("content")
        self.finish_and_pass_qa(content)
        other=self.client.post("/workspaces/demo/tool-jobs",json={
            "action":"postiz.create_post",
            "payload":{"mode":"draft","content":"Nội dung demo chính xác, đang chờ kiểm tra thật.",
                       "channel_ids":["unlisted-chan"],"source_task_id":content["task_id"]},
            "idempotency_key":"content-wrong-channel"})
        self.assertEqual(other.status_code,403,other.text)

    def test_retry_budget_and_work_order_observability(self):
        order=self.create_order("content")
        start=self.client.post("/workspaces/demo/agent-work-orders/"+order["id"]+
                               "/computer-pilot",json={"max_steps":5}).json()
        repeated=[{"tool":"list_files","args":{"path":"."},"status":"failed"}]*2
        with self.assertRaises(Exception):
            pilot.assert_pilot_action(start,"list_files",{"path":"."},repeated)
        with self.assertRaises(Exception):
            pilot.assert_pilot_action(start,"browser_fill",{"selector":"#post","text":"abc"},
                [{"tool":"browser_fill","args":{"selector":"#post","text":"abc"},
                  "status":"completed"}])
        metrics=self.client.get("/agents/runtime-pilot/metrics?workspace_id=demo")
        self.assertEqual(metrics.status_code,200,metrics.text)
        values=metrics.json()
        self.assertEqual(values["sample_size"],1)
        self.assertEqual(values["external_verified_orders"],0)
        self.assertFalse(values["computer_model_cost_included"])

    def test_ui_markup_and_javascript(self):
        r=self.client.get("/agents/runtime-pilot/ui")
        self.assertEqual(r.status_code,200,r.text)
        self.assertIn('id="handoff"',r.text)
        self.assertIn('id="verify"',r.text)
        self.assertNotIn("eval(",r.text)
        node=shutil.which("node")
        if not node:self.skipTest("Node not installed")
        block=re.search(r"<script>(.*?)</script>",r.text,re.S)
        self.assertIsNotNone(block)
        with tempfile.NamedTemporaryFile(mode="w",suffix=".js",encoding="utf-8") as f:
            f.write(block.group(1));f.flush()
            check=subprocess.run([node,"--check",f.name],capture_output=True,text=True)
        self.assertEqual(check.returncode,0,check.stderr)


if __name__=="__main__":
    unittest.main()
