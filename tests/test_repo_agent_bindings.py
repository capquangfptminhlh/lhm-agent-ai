"""Contract tests for project-specific repo agent ownership and live CI input."""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

import app
import core
import agent_work_orders
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
        self.assertIn("Chẩn đoán CI", task["instruction"])


if __name__ == "__main__":
    unittest.main()
