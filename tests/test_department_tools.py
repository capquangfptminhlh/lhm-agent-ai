"""16 fixed department tools: identity, QA, approval, real webhook payload and receipt."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

import app
import core
import tool_hub
import agent_work_orders as work
import department_tools as departments
from publication_gate import record_review


class DepartmentIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="lhm_dept_test_")
        self.addCleanup(self.tmp.cleanup)
        old_db = core.DATABASE
        core.DATABASE = Path(self.tmp.name) / "agent.sqlite3"
        self.addCleanup(setattr, core, "DATABASE", old_db)
        core.setup()
        tool_hub.prepare_db()
        work.prepare_db()
        self.env=patch.dict(os.environ,{
            "LHM_ADMIN_TOKEN":"","LHM_TOOLS_WORKSPACE":"demo",
            "LHM_OPENROUTER_FREE_ONLY":"1","OPENROUTER_API_KEY":"fake-test-key",
            "LHM_DEPARTMENT_CONTENT_WEBHOOK_URL":"https://workflows.example.net/webhook/content",
            "LHM_DEPARTMENT_FINANCE_WEBHOOK_URL":"https://workflows.example.net/webhook/finance",
            "LHM_DEPARTMENT_HR_WEBHOOK_URL":"https://workflows.example.net/webhook/hr",
        },clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.client=TestClient(app.app)

    def new_order(self,role="content"):
        d=self.client.post("/workspaces/demo/agents/"+role+"/work-orders",json={
            "objective":"Soạn kế hoạch phòng ban với dữ liệu đã kiểm tra rõ nguồn",
            "idempotency_key":"role-"+role+"-test-12345"})
        self.assertEqual(d.status_code,201,d.text)
        return d.json()

    def finish(self, order):
        with core.db() as c:
            c.execute("UPDATE tasks SET status='completed',result=? WHERE id=?",
                      ("Báo cáo kết quả đã rà soát và chờ phê duyệt.",order["task_id"]))

    def qa_pass(self,order):
        r=self.client.post("/workspaces/demo/agent-work-orders/"+order["id"]+"/quality-review")
        self.assertEqual(r.status_code,201,r.text)
        qa_id=r.json()["qa_task_id"]
        review='{"verdict":"pass","reason":"Đã kiểm tra phạm vi thử nghiệm"}'
        with core.db() as c:
            c.execute("UPDATE tasks SET status='completed',result=? WHERE id=?",(review,qa_id))
            record_review(c,{"id":qa_id,"role":"quality",
                             "workspace_id":"demo","depends_on":order["task_id"]},review)

    def handoff(self,order,role="content",key="department-fixed-flow-12345"):
        return self.client.post("/workspaces/demo/agents/"+role+"/department-tool-jobs",json={
            "work_order_id":order["id"],"idempotency_key":key})

    def test_contract_has_every_role_and_real_provider_links_without_claiming_connected(self):
        r=self.client.get("/agents/departments")
        self.assertEqual(r.status_code,200,r.text)
        d=r.json()
        self.assertEqual(d["count"],16)
        self.assertEqual({x["role"] for x in d["agents"]},set(core.ROLES))
        self.assertFalse(d["externally_verified"])
        for a in d["agents"]:
            self.assertIn(a["department_workflow"]["action"],tool_hub.TOOL_ACTIONS)
            self.assertTrue(a["primary_tool"]["source_repo"].startswith("https://"))
            self.assertTrue(a["existing_actions"])
            for x in a["existing_actions"]:
                self.assertIn(x["action"],tool_hub.TOOL_ACTIONS)
                self.assertFalse(x["connection_verified"])
            self.assertTrue(a["department_workflow"]["requires_quality_review"])
            self.assertFalse(a["department_workflow"]["connection_verified"])
            self.assertTrue(a["department_workflow"]["requires_human_approval"])
        self.assertNotIn("webhook/content",r.text)
        self.assertNotIn("fake-test-key",r.text)

    def test_unconnected_and_invalid_workflow_urls_fail_closed(self):
        with patch.dict(os.environ,{"LHM_DEPARTMENT_FINANCE_WEBHOOK_URL":""}):
            item=self.client.get("/agents/departments").json()["agents"]
            finance=next(a for a in item if a["role"]=="finance")
            self.assertFalse(finance["department_workflow"]["configured"])
            order=self.new_order("finance")
            self.finish(order)
            self.qa_pass(order)
            result=self.handoff(order,"finance")
            self.assertEqual(result.status_code,409,result.text)
        for url in ["file:///etc/passwd","http://attacker.example.com/h",
                    "https://someone:password@evil.example.net/webhook",
                    "https://example.org/webhook?secret=abc"]:
            with patch.dict(os.environ,{"LHM_DEPARTMENT_CONTENT_WEBHOOK_URL":url}):
                self.assertFalse(departments.configured("content"),url)
        self.assertEqual(self.client.get("/agents/departments/ui").status_code,200)

    def test_no_handoff_before_content_and_qa(self):
        order=self.new_order()
        first=self.handoff(order)
        self.assertEqual(first.status_code,409,first.text)
        self.finish(order)
        second=self.handoff(order)
        self.assertEqual(second.status_code,409,second.text)
        self.qa_pass(order)
        good=self.handoff(order)
        self.assertEqual(good.status_code,201,good.text)
        record=good.json()
        self.assertEqual(record["action"],"department.run.content")
        self.assertEqual(record["status"],"awaiting_approval")
        self.assertFalse(record["external_action_completed"])
        self.assertEqual(self.client.post("/tool-jobs/"+record["id"]+"/execute").status_code,409)
        with core.db() as c:
            row=c.execute("SELECT status,result_json FROM tool_jobs WHERE id=?",
                          (record["id"],)).fetchone()
        self.assertEqual(row["status"],"awaiting_approval")
        self.assertIsNone(row["result_json"])

    def test_prevent_role_impersonation_and_cross_workspace(self):
        order=self.new_order()
        self.finish(order)
        self.qa_pass(order)
        cross=self.handoff(order,"finance",key="cross-role-attempt")
        self.assertEqual(cross.status_code,404,cross.text)
        other=self.client.post("/workspaces/not-a-workspace/agents/content/department-tool-jobs",
            json={"work_order_id":order["id"],"idempotency_key":"cross-workspace-test"})
        self.assertIn(other.status_code,(403,404))
        r=self.client.post("/workspaces/demo/tool-jobs",json={
            "action":"department.run.finance",
            "payload":{"role":"finance","work_order_id":order["id"],
                       "source_task_id":order["task_id"],
                       "content":"Báo cáo kết quả đã rà soát và chờ phê duyệt."},
            "idempotency_key":"forged-role-action-test"})
        self.assertEqual(r.status_code,409,r.text)
        unknown=self.client.post("/workspaces/demo/tool-jobs",json={
            "action":"department.run.fake","payload":{},"idempotency_key":"forged-fake-role"})
        self.assertEqual(unknown.status_code,422,unknown.text)

    def test_content_mutation_revokes_approved_tool(self):
        order=self.new_order()
        self.finish(order)
        self.qa_pass(order)
        record=self.handoff(order).json()
        approval=self.client.post("/tool-jobs/"+record["id"]+"/approve")
        self.assertEqual(approval.status_code,200,approval.text)
        with core.db() as c:
            c.execute("UPDATE tasks SET result=? WHERE id=?",("Bản nội dung bị thay đổi",order["task_id"]))
        with patch.object(departments,"dispatch") as remote:
            stopped=self.client.post("/tool-jobs/"+record["id"]+"/execute")
            self.assertEqual(stopped.status_code,409,stopped.text)
            remote.assert_not_called()

    def test_real_http_webhook_path_is_used_only_after_qa_and_owner_approval(self):
        order=self.new_order("finance")
        self.finish(order)
        self.qa_pass(order)
        first=self.handoff(order,"finance",key="unique-finance-1199")
        self.assertEqual(first.status_code,201,first.text)
        job=first.json()
        second=self.handoff(order,"finance",key="unique-finance-1199")
        self.assertTrue(second.json()["deduplicated"])
        self.assertEqual(second.json()["id"],job["id"])
        self.assertEqual(self.client.post("/tool-jobs/"+job["id"]+"/approve").status_code,200)
        sends=[]
        def handler(request):
            sends.append(request)
            return httpx.Response(202,json={"accepted":True})
        transport=httpx.MockTransport(handler)
        original=httpx.AsyncClient
        with patch.object(departments.httpx,"AsyncClient",
                          side_effect=lambda *a,**kw: original(*a,transport=transport,**kw)):
            result=self.client.post("/tool-jobs/"+job["id"]+"/execute")
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(len(sends),1)
        self.assertEqual(sends[0].url.host,"workflows.example.net")
        self.assertEqual(sends[0].url.path,"/webhook/finance")
        body=json.loads(sends[0].content)
        self.assertEqual(body["role"],"finance")
        self.assertEqual(body["content"],"Báo cáo kết quả đã rà soát và chờ phê duyệt.")
        self.assertEqual(body["task_id"],order["task_id"])
        self.assertEqual(result.json()["status"],"accepted_unverified")
        self.assertEqual(result.json()["receipt"]["confirmation"],"workflow_triggered_unverified")
        self.assertNotIn(chr(34)+"accepted"+chr(34)+":true",result.text.lower())
        # No duplicate external side effects on repeated POST.
        repeated=self.client.post("/tool-jobs/"+job["id"]+"/execute")
        self.assertEqual(repeated.status_code,409)
        self.assertEqual(len(sends),1)

    def test_ui_javascript_syntax(self):
        html=self.client.get("/agents/departments/ui")
        self.assertEqual(html.status_code,200)
        self.assertIn('id="roles"',html.text)
        self.assertIn('id="approve"',html.text)
        self.assertNotIn("eval(",html.text)
        self.assertNotIn("innerHTML",html.text)
        node=shutil.which("node")
        if not node:self.skipTest("node unavailable")
        block=re.search(r"<script>(.*?)</script>",html.text,re.S)
        self.assertIsNotNone(block)
        with tempfile.NamedTemporaryFile(mode="w",suffix=".js",encoding="utf-8") as tmp:
            tmp.write(block.group(1));tmp.flush()
            result=subprocess.run([node,"--check",tmp.name],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)


if __name__=="__main__":
    unittest.main()
