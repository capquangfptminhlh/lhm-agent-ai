"""Offline Free-only regression for EVERY primary task-creation pathway.

No live model or external provider calls. Tests use disposable SQLite.
"""
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
import workflow_builder as builder
import tool_hub


class FreeOnlyTaskEntryPoints(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="lhm-all-free-tasks-")
        self.old_db=core.DATABASE
        core.DATABASE=Path(self.temp.name)/"db.sqlite3"
        core.setup()
        tool_hub.prepare_db()
        builder.prepare_db()
        self.env=patch.dict(os.environ,{
            "LHM_ADMIN_TOKEN":"",
            "LHM_OPENROUTER_FREE_ONLY":"1",
            "OPENROUTER_API_KEY":"offline-test-key",
            "DEEPSEEK_API_KEY":"",
            "LHM_TOOLS_WORKSPACE":"demo",
        })
        self.env.start()
        self.client=TestClient(app.app)

    def tearDown(self):
        self.env.stop()
        core.DATABASE=self.old_db
        self.temp.cleanup()

    def assert_free(self,task_ids):
        with core.db() as c:
            tasks=c.execute(
                "SELECT id,model FROM tasks WHERE id IN ("+
                ",".join("?" for _ in task_ids)+")",tuple(task_ids)).fetchall()
        self.assertEqual(len(tasks),len(task_ids))
        self.assertTrue(all(t["model"]=="openrouter/free" for t in tasks),
                        [dict(t) for t in tasks])

    def test_health_state_registry_and_provider_match_effective_free_model(self):
        with patch.object(app,"MODEL","deepseek-flash"):
            for endpoint in ("/health","/llm/providers"):
                res=self.client.get(endpoint)
                self.assertEqual(res.status_code,200,res.text)
                self.assertEqual(res.json()["active_model"],"openrouter/free")
                self.assertNotIn("offline-test-key",res.text)
            state=self.client.get("/state")
            self.assertEqual(state.status_code,200,state.text)
            self.assertEqual(state.json()["runtime"]["active_model"],"openrouter/free")
            self.assertEqual(state.json()["identity"]["model_id"],"openrouter/free")
            self.assertEqual(set(state.json()["active"].values()),{"openrouter/free"})
            registry=self.client.get("/registry")
            self.assertEqual(registry.status_code,200,registry.text)
            self.assertEqual(list(registry.json()["models"]),["openrouter/free"])

    def test_campaign_and_builtin_workflow_use_free_model_without_calls(self):
        with patch.object(app,"MODEL","deepseek-flash"):
            campaign=self.client.post("/workspaces/demo/campaigns",json={
                "objective":"Lên kế hoạch thương mại giả định có QA",
                "mode":"full","context":"Dữ liệu mô phỏng",
                "idempotency_key":"all-free-campaign-2026"})
            self.assertEqual(campaign.status_code,201,campaign.text)
            workflow=self.client.post("/workspaces/demo/workflows",json={
                "process":"marketing","objective":"Soạn chiến dịch mẫu và duyệt nội bộ",
                "idempotency_key":"all-free-workflow-2026"})
            self.assertEqual(workflow.status_code,201,workflow.text)
        ids=[t["id"] for t in campaign.json()["pipeline"]+workflow.json()["pipeline"]]
        self.assertEqual(len(ids),7)
        self.assert_free(ids)
        with core.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM tool_jobs").fetchone()[0],0)

    def test_workflow_builder_uses_free_model_for_all_agent_steps(self):
        template=self.client.post("/workspaces/demo/builder/templates",json={
            "name":"Bản thử Free-only",
            "steps":[
                {"type":"agent","role":"research","instruction":"Tìm thông tin thị trường giả định"},
                {"type":"agent","role":"content","instruction":"Soạn bài theo dữ liệu giả định"},
                {"type":"agent","role":"quality","instruction":"Kiểm tra các tuyên bố và nguồn"},
            ]})
        self.assertEqual(template.status_code,201,template.text)
        with patch.object(app,"MODEL","deepseek-flash"):
            run=self.client.post(
                "/workspaces/demo/builder/templates/"+template.json()["id"]+"/run",
                json={"objective":"Tạo bản mô phỏng để kiểm tra 3 Agent",
                      "idempotency_key":"all-free-builder-2026"})
        self.assertEqual(run.status_code,201,run.text)
        ids=[t["id"] for t in run.json()["tasks"]]
        self.assertEqual(len(ids),3)
        self.assert_free(ids)

    def test_hermes_chat_uses_free_and_records_real_mocked_draft(self):
        response={
            "choices":[{"message":{"content":"Bản nháp phản hồi giả định, chưa gửi ra ngoài."}}],
            "usage":{"prompt_tokens":12,"completion_tokens":18,"cost":0},
        }
        with patch.object(app,"MODEL","deepseek-flash"):
            with patch.object(app,"deepseek_request",new=AsyncMock(return_value=response)) as call:
                result=self.client.post("/v1/chat/completions",json={
                    "role":"director","stream":False,
                    "messages":[{"role":"user","content":"Lập kế hoạch an toàn cho cửa hàng"}]})
            self.assertEqual(result.status_code,200,result.text)
            self.assertEqual(result.json()["model"],"openrouter/free")
            self.assertIn("chưa gửi",result.json()["choices"][0]["message"]["content"])
            self.assertEqual(call.await_count,1)
            self.assertEqual(call.await_args.args[-1],"openrouter/free")
            self.assert_free([result.json()["id"]])

    def test_explicit_paid_model_is_denied_before_insert_not_rewritten(self):
        with core.db() as c:
            with self.assertRaises(HTTPException) as caught:
                app.task_record(c,"demo","research","Tạo kế hoạch mô phỏng","",
                                model="deepseek-flash")
        self.assertEqual(caught.exception.status_code,422)
        with core.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM tasks").fetchone()[0],0)


if __name__=="__main__":
    unittest.main()
