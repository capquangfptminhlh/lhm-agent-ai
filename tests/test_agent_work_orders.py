"""Acceptance for 16 fixed specialist missions and honest real-worker delivery."""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import app
import core
import agent_work_orders as work
import worker
from tool_hub import prepare_db as prepare_tools


class FixedAgentWorkTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="lhm_fixed_agent_")
        self.addCleanup(self.tmp.cleanup)
        self.old=core.DATABASE
        core.DATABASE=Path(self.tmp.name)/"db.sqlite3"
        self.addCleanup(setattr,core,"DATABASE",self.old)
        core.setup()
        work.prepare_db()
        prepare_tools()
        self.env=patch.dict(os.environ,{
          "LHM_ADMIN_TOKEN":"","LHM_OPENROUTER_FREE_ONLY":"1",
          "OPENROUTER_API_KEY":"offline-test-key","LHM_TOOLS_WORKSPACE":"demo",
          "LHM_POSTIZ_API_KEY":"","LHM_WP_URL":"","LHM_WP_USER":"",
          "LHM_WP_APP_PASSWORD":"","LHM_CRAWL4AI_URL":"",
          "LHM_TWENTY_API_KEY":"","LHM_CHATWOOT_API_TOKEN":"",
        })
        self.env.start();self.addCleanup(self.env.stop)
        self.client=TestClient(app.app)

    def test_every_role_has_exactly_one_fixed_job_with_matching_repo(self):
        resp=self.client.get("/agents/fixed-jobs")
        self.assertEqual(resp.status_code,200,resp.text)
        profiles=resp.json()["agents"]
        self.assertEqual(len(profiles),16)
        self.assertEqual({a["role"] for a in profiles},set(core.ROLES))
        seen=set()
        for a in profiles:
            self.assertNotIn(a["role"],seen)
            seen.add(a["role"])
            j=a["fixed_job"];i=a["integration"]
            self.assertEqual(work.JOBS[j["id"]]["role"],a["role"])
            self.assertTrue(j["acceptance"])
            self.assertTrue(i["source_repo"].startswith("https://"))
            self.assertFalse(i["connection_verified"])
            self.assertEqual(i["scope"],"reference_only_until_provider_verified")
            self.assertEqual(a["outputs"],"internal_draft_not_external_execution")
            self.assertTrue(a["external_actions_require_approval"])

    def test_job_is_locked_to_specialty_and_idempotent(self):
        body={"objective":"Viết một bài SEO có thể kiểm tra lại dữ liệu",
              "context":"Sản phẩm mẫu, chưa có giá thật",
              "idempotency_key":"fixed-content-0001"}
        url="/workspaces/demo/agents/content/work-orders"
        first=self.client.post(url,json=body)
        self.assertEqual(first.status_code,201,first.text)
        v=first.json()
        self.assertEqual(v["role"],"content")
        self.assertEqual(v["fixed_job"]["id"],"content.01")
        self.assertEqual(v["task_status"],"queued")
        self.assertEqual(v["work_status"],"queued")
        self.assertFalse(v["external_action_completed"])
        with core.db() as c:
            t=c.execute("SELECT role,instruction,model FROM tasks WHERE id=?",(v["task_id"],)).fetchone()
        self.assertEqual(t["role"],"content")
        self.assertEqual(t["model"],"openrouter/free")
        self.assertIn("Viết SEO và bài blog",t["instruction"])
        self.assertIn("không có công cụ và biên nhận",t["instruction"])
        again=self.client.post(url,json=body)
        self.assertEqual(again.status_code,201)
        self.assertEqual(again.json()["id"],v["id"])
        self.assertTrue(again.json()["deduplicated"])
        altered=self.client.post(url,json={**body,"objective":"Mục tiêu khác bất kỳ có độ dài hợp lệ"})
        self.assertEqual(altered.status_code,409)
        self.assertEqual(self.client.get("/workspaces/demo/agent-work-orders").json()["orders"][0]["id"],v["id"])

    def test_queued_work_actually_runs_through_worker_but_never_claims_publication(self):
        body={"objective":"Viết bài SEO giới thiệu sản phẩm dùng dữ liệu thử nghiệm",
              "idempotency_key":"fixed-workend-001"}
        r=self.client.post("/workspaces/demo/agents/content/work-orders",json=body)
        self.assertEqual(r.status_code,201,r.text)
        taskid=r.json()["task_id"]
        sample="1. Công việc đã phân tích\n2. Sản phẩm bàn giao: bản nháp\n3. Nguồn: chưa kiểm chứng\n4. Cần duyệt"
        reply={"choices":[{"message":{"content":sample}}],
               "usage":{"prompt_tokens":12,"completion_tokens":25,"cost":0}}
        # Only the LLM network is stubbed. Worker, queue, SQLite and API are real.
        with patch.object(app,"deepseek_request",new=AsyncMock(return_value=reply)) as model:
            with core.db() as c:
                pending=c.execute("SELECT id,role,workspace_id,model FROM tasks WHERE id=?",(taskid,)).fetchone()
            n=asyncio.run(worker.dispatch_agent_batch([pending]))
            self.assertEqual(n,1)
            model.assert_awaited_once()
        state=self.client.get("/workspaces/demo/agent-work-orders/"+r.json()["id"])
        self.assertEqual(state.status_code,200,state.text)
        record=state.json()
        self.assertEqual(record["task_status"],"completed")
        self.assertEqual(record["work_status"],"draft_ready_not_verified")
        self.assertIn("bản nháp",record["draft"])
        self.assertIs(record["external_action_completed"],False)
        self.assertIsNone(record["external_receipt"])
        with core.db() as c:
            external=c.execute("SELECT COUNT(*) FROM tool_jobs").fetchone()[0]
        self.assertEqual(external,0)

    def test_isolation_and_fail_closed_validation(self):
        with self.assertRaises(Exception):
            work.role_profile("phantom")
        self.assertEqual(self.client.post("/workspaces/absent/agents/content/work-orders",
                         json={"objective":"Một mục tiêu hợp lệ vượt quá mười ký tự",
                               "idempotency_key":"not-valid-workspace"}).status_code,404)
        self.assertEqual(self.client.post("/workspaces/demo/agents/phantom/work-orders",
                         json={"objective":"Một mục tiêu hợp lệ vượt quá mười ký tự",
                               "idempotency_key":"not-valid-agent"}).status_code,404)
        self.assertEqual(self.client.post("/workspaces/demo/agents/ads/work-orders",
                         json={"objective":"abc","idempotency_key":"demo-long-key"}).status_code,422)
        self.assertEqual(self.client.get("/workspaces/demo/agent-work-orders/bad").status_code,404)

    def test_html_is_local_no_source_repos_executed(self):
        r=self.client.get("/agent-work")
        self.assertEqual(r.status_code,200)
        text=r.text
        self.assertIn('id="submit"',text)
        self.assertIn('id="role"',text)
        self.assertIn("external",text)
        self.assertNotIn("eval(",text)
        self.assertNotIn("innerHTML",text)


if __name__=="__main__":
    unittest.main()
