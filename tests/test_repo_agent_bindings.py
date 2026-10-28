"""Contract tests for project-specific repo agent ownership and live CI input."""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

import app
import core
import agent_work_orders
import worker
import repo_agent_bindings as bindings


class RepoAgentsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        old = core.DATABASE
        core.DATABASE = Path(self.temp.name) / "agent.sqlite3"
        self.addCleanup(setattr, core, "DATABASE", old)
        core.setup()
        agent_work_orders.prepare_db()
        env = patch.dict(os.environ, {
            "LHM_ADMIN_TOKEN": "", "LHM_GITHUB_READ_TOKEN": "",
            "LHM_OPENROUTER_FREE_ONLY": "1", "OPENROUTER_API_KEY": "offline-key"})
        env.start()
        self.addCleanup(env.stop)
        self.client = TestClient(app.app)

    def test_all_owners_have_one_fixed_repo_and_known_role(self):
        data = self.client.get("/repo-agents")
        self.assertEqual(data.status_code, 200, data.text)
        agents = data.json()["agents"]
        self.assertGreaterEqual(len(agents), 8)
        names = {entry["id"] for entry in agents}
        self.assertEqual(len(names), len(agents))
        repos = {entry["repository"] for entry in agents}
        self.assertEqual(len(repos), len(agents))
        for agent in agents:
            self.assertIn(agent["role"], core.ROLES)
            self.assertTrue(agent["repository"].startswith("capquangfptminhlh/"))
            self.assertEqual(agent["job_type"], "github_actions_ci_triage")
            self.assertFalse(agent["writes_enabled"])
        self.assertEqual(self.client.get("/repo-agents/bad/ci").status_code, 404)

    def test_repo_agent_router_has_no_duplicate_api_routes(self):
        paths=[(route.path,tuple(sorted(route.methods or [])))
               for route in app.app.routes if route.path.startswith("/repo-agents")
               or "/repo-agents/" in route.path]
        self.assertEqual(len(paths),len(set(paths)))

    def test_live_check_uses_only_predefined_repo_read_only_and_returns_real_run(self):
        calls = []
        def handler(req):
            calls.append(req)
            return httpx.Response(200, json={"workflow_runs": [{
                "id": 12345, "name": "Quality Gate", "status": "completed",
                "conclusion": "failure", "head_sha": "abc123",
                "html_url": "https://malicious.example/never-used"}]})
        transport = httpx.MockTransport(handler)
        original = httpx.AsyncClient
        def create_client(*a, **kw):
            kw["transport"] = transport
            return original(*a, **kw)
        with patch.object(bindings.httpx, "AsyncClient", side_effect=create_client):
            result = self.client.get("/repo-agents/agent-cnlg/ci")
        self.assertEqual(result.status_code, 200, result.text)
        payload = result.json()
        self.assertEqual(payload["repository"], "capquangfptminhlh/cnlg")
        self.assertTrue(payload["connection_verified"])
        self.assertFalse(payload["can_create_pr"])
        self.assertEqual(payload["runs"][0]["conclusion"], "failure")
        self.assertEqual(payload["runs"][0]["html_url"],
                         "https://github.com/capquangfptminhlh/cnlg/actions/runs/12345")
        self.assertEqual(calls[0].url.host, "api.github.com")
        self.assertEqual(calls[0].url.params.get("branch"),"main")
        self.assertEqual(calls[0].method, "GET")
        self.assertNotIn("authorization", calls[0].headers)

    def test_private_repo_denied_does_not_pretend_connected(self):
        transport = httpx.MockTransport(lambda req: httpx.Response(404))
        client_cls = httpx.AsyncClient
        with patch.object(bindings.httpx, "AsyncClient",
                          side_effect=lambda *a, **kw: client_cls(*a, transport=transport, **kw)):
            result = self.client.get("/repo-agents/agent-zalo-crm/ci")
        self.assertEqual(result.status_code, 424)
        self.assertIn("token", result.json()["detail"].lower())

    def test_real_queue_from_ci_is_idempotent_and_never_claims_code_written(self):
        sample = {
            "agent_id": "agent-autotax",
            "repository": "capquangfptminhlh/autotax",
            "runs": [{
                "id": 9876, "name": "QA", "status": "completed",
                "conclusion": "failure", "head_sha": "abc",
                "html_url": "https://github.com/capquangfptminhlh/autotax/actions/runs/9876"}]}
        with patch.object(bindings, "check_ci", new=AsyncMock(return_value=sample)):
            first = self.client.post("/repo-agents/agent-autotax/diagnose",
                                     json={"workspace": "demo"})
            second = self.client.post("/repo-agents/agent-autotax/diagnose",
                                      json={"workspace": "demo"})
        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(first.json()["id"], second.json()["id"])
        self.assertEqual(first.json()["role"], "quality")
        self.assertFalse(first.json()["code_changed"])
        self.assertFalse(first.json()["pull_request_created"])
        with core.db() as c:
            task = c.execute("SELECT status,model,instruction,context FROM tasks WHERE id=?",
                             (first.json()["task_id"],)).fetchone()
        self.assertEqual(task["status"], "queued")
        self.assertEqual(task["model"], "openrouter/free")
        self.assertIn("autotax", task["context"])
        self.assertIn("CHẨN ĐOÁN CI CỐ ĐỊNH", task["instruction"])
        self.assertNotIn("NGHỀ CỐ ĐỊNH:",task["instruction"])
        self.assertEqual(first.json()["fixed_job"]["type"],"github_actions_ci_triage")
        detail=self.client.get("/workspaces/demo/repo-agents/agent-autotax/diagnoses/"+first.json()["id"])
        self.assertEqual(detail.status_code,200,detail.text)
        self.assertEqual(detail.json()["run_id"],9876)
        self.assertEqual(self.client.get("/workspaces/demo/repo-agents/agent-cnlg/diagnoses/"+first.json()["id"]).status_code,404)

    def test_live_job_step_failure_is_bounded_and_provenance_is_owned_repo(self):
        calls=[]
        def handler(req):
            calls.append(req.url.path)
            if req.url.path.endswith("/actions/runs"):
                return httpx.Response(200,json={"workflow_runs":[{
                    "id":789,"name":"Build","status":"completed","conclusion":"failure",
                    "head_branch":"main","head_sha":"a"*40,
                    "html_url":"https://attacker.invalid/forged"}]})
            if req.url.path.endswith("/actions/runs/789/jobs"):
                return httpx.Response(200,json={"jobs":[{
                    "id":431,"name":"Windows compile","conclusion":"failure",
                    "html_url":"https://attacker.invalid/job",
                    "steps":[{"name":"Checkout","conclusion":"success"},
                             {"name":"Package EXE","conclusion":"failure"}]}]})
            return httpx.Response(404)
        transport=httpx.MockTransport(handler)
        client=httpx.AsyncClient
        with patch.object(bindings.httpx,"AsyncClient",
                          side_effect=lambda *a,**kw:client(*a,transport=transport,**kw)):
            resp=self.client.get("/repo-agents/agent-browser/ci")
        self.assertEqual(resp.status_code,200,resp.text)
        d=resp.json()
        self.assertTrue(d["job_details_verified"])
        self.assertEqual(d["failed_jobs"][0]["failed_steps"],["Package EXE"])
        self.assertEqual(d["failed_jobs"][0]["html_url"],
                         "https://github.com/capquangfptminhlh/ggminh/actions/runs/789/job/431")
        self.assertEqual(d["runs"][0]["head_branch"],"main")
        self.assertEqual(d["branch_scope"],"main")
        self.assertEqual(len(calls),2)
        self.assertTrue(all(path.startswith("/repos/capquangfptminhlh/ggminh/actions/")
                            for path in calls))

    def test_unavailable_job_details_are_not_fabricated_as_passing(self):
        client=httpx.AsyncClient
        def handler(req):
            if req.url.path.endswith("/actions/runs"):
                return httpx.Response(200,json={"workflow_runs":[{
                    "id":991,"name":"QA","status":"completed","conclusion":"success"}]})
            return httpx.Response(403)
        with patch.object(bindings.httpx,"AsyncClient",
                          side_effect=lambda *a,**kw:client(*a,transport=httpx.MockTransport(handler),**kw)):
            resp=self.client.get("/repo-agents/agent-cnlg/ci")
        self.assertEqual(resp.status_code,200,resp.text)
        self.assertFalse(resp.json()["job_details_verified"])
        self.assertEqual(resp.json()["failed_jobs"],[])
        self.assertEqual(resp.json()["runs"][0]["conclusion"],"success")

    def test_real_worker_produces_retrievable_report_not_fake_code_changes(self):
        payload={
            "agent_id":"agent-autotax","repository":"capquangfptminhlh/autotax",
            "job":"Kiểm tra các quality gate AutoTax POS",
            "runs":[{"id":7777,"name":"Quality Gate","head_branch":"main",
                     "status":"completed","conclusion":"failure",
                     "head_sha":"a"*40,
                     "html_url":"https://github.com/capquangfptminhlh/autotax/actions/runs/7777"}],
            "job_details_verified":True,
            "failed_jobs":[{"job":"Build","conclusion":"failure",
                            "failed_steps":["Unit tests failed"],"html_url":None}]
        }
        with patch.object(bindings,"check_ci",new=AsyncMock(return_value=payload)):
            started=self.client.post("/repo-agents/agent-autotax/diagnose",json={"workspace":"demo"})
        self.assertEqual(started.status_code,201,started.text)
        d=started.json()
        sample="CI 7777: Unit tests failed, cần xem log và tái chạy. Chưa sửa code."
        response={"choices":[{"message":{"content":sample}}],
                  "usage":{"prompt_tokens":8,"completion_tokens":14,"cost":0}}
        with core.db() as c:
            task=c.execute("SELECT id,role,workspace_id,model,context FROM tasks WHERE id=?",
                           (d["task_id"],)).fetchone()
            self.assertIn("Unit tests failed",task["context"])
        with patch.object(app,"deepseek_request",new=AsyncMock(return_value=response)) as model:
            done=asyncio.run(worker.dispatch_agent_batch([task]))
        self.assertEqual(done,1)
        model.assert_awaited_once()
        report=self.client.get("/workspaces/demo/repo-agents/agent-autotax/diagnoses/"+d["id"])
        self.assertEqual(report.status_code,200,report.text)
        self.assertEqual(report.json()["task_status"],"completed")
        self.assertEqual(report.json()["report"],sample)
        self.assertTrue(report.json()["report_is_draft"])
        self.assertFalse(report.json()["code_changed"])
        self.assertFalse(report.json()["pull_request_created"])
        items=self.client.get("/workspaces/demo/repo-agents/agent-autotax/diagnoses")
        self.assertEqual(len(items.json()["diagnoses"]),1)

    def test_repository_agents_html_scripts_are_valid(self):
        html=self.client.get("/repo-agents/ui").text
        self.assertIn('textContent',html)
        self.assertIn('Xem báo cáo',html)
        self.assertIn('function report(item,button)',html)
        node=shutil.which("node")
        if not node:
            self.skipTest("Node not installed")
        script=re.search(r"<script>(.*?)</script>",html,re.S)
        self.assertIsNotNone(script)
        with tempfile.NamedTemporaryFile(mode="w",suffix=".js",encoding="utf-8") as f:
            f.write(script.group(1));f.flush()
            chk=subprocess.run([node,"--check",f.name],capture_output=True,text=True)
        self.assertEqual(chk.returncode,0,chk.stderr)


if __name__ == "__main__":
    unittest.main()
