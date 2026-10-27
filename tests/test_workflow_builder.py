"""Workflow Builder integration tests. No remote LLM or publishing calls."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app
import core
import tool_hub
import workflow_builder as builder
from publication_gate import record_review


def stages(action: str | None = None):
    steps = [
        {"type":"agent","role":"research","instruction":"Nghiên cứu thị trường có nguồn rõ ràng"},
        {"type":"agent","role":"content","instruction":"Viết nội dung quảng cáo từ dữ liệu đã kiểm chứng"},
        {"type":"agent","role":"quality","instruction":"Kiểm tra nguồn và độ chính xác"},
    ]
    if action:
        step={"type":"tool","action":action}
        if action=="postiz.create_post":
            step["mode"]="draft"
            step["channel_ids"]=["real-connected-channel-id"]
        else:
            step["title"]="Bài thử nghiệm QA"
            step["status"]="draft"
        steps.append(step)
    return steps


class WorkflowBuilderTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="lhm_builder_test_")
        self.old=core.DATABASE
        core.DATABASE=Path(self.tmp.name)/"test.sqlite3"
        core.setup()
        tool_hub.prepare_db()
        builder.prepare_db()
        self.env=patch.dict(os.environ, {"LHM_ADMIN_TOKEN":"","LHM_TOOLS_WORKSPACE":"demo"})
        self.env.start()
        self.client=TestClient(app.app)

    def tearDown(self):
        self.env.stop()
        core.DATABASE=self.old
        self.tmp.cleanup()

    def template(self, steps, ws="demo", name="Biên tập đa nền tảng"):
        response=self.client.post(f"/workspaces/{ws}/builder/templates",
            json={"name":name,"description":"Nội dung có phê duyệt","steps":steps})
        self.assertEqual(response.status_code,201,response.text)
        return response.json()

    def launch(self, template_id, ws="demo", idem="workflow-test-123"):
        r=self.client.post(f"/workspaces/{ws}/builder/templates/{template_id}/run",
            json={"objective":"Tạo nội dung tư vấn có nguồn được kiểm chứng","context":"Không dùng thông tin riêng tư",
                  "idempotency_key":idem})
        self.assertEqual(r.status_code,201,r.text)
        return r.json()

    def mark_complete(self, ids, quality="pass"):
        with core.db() as c:
            for idx,tid in enumerate(ids):
                t=c.execute("SELECT * FROM tasks WHERE id=?",(tid,)).fetchone()
                text='{"verdict":"'+quality+'","reason":"Verified offline unit test"}' if t["role"]=="quality" else (
                    "Nội dung do Agent tạo: bài viết dự án theo chứng cứ" if t["role"]=="content" else "Research nguồn đáng tin")
                c.execute("UPDATE tasks SET status='completed',result=? WHERE id=?",(text,tid))
                if t["role"]=="quality":
                    record_review(c,dict(t),text)

    def test_worker_background_sync_runs_without_browser_session(self):
        """Regression: worker must invoke builder sync, not leave it commented out."""
        import asyncio
        from unittest.mock import AsyncMock
        import worker

        class WorkerCycleComplete(Exception):
            pass

        async def finish_loop(*args, **kwargs):
            raise WorkerCycleComplete()

        with patch.object(worker,"recover_stuck",new_callable=AsyncMock) as recovery:
            with patch.object(worker,"sync_ready_builder_runs") as sync:
                with patch.object(worker,"process_one_approved_tool",new_callable=AsyncMock) as tool:
                    with patch.object(worker.asyncio,"sleep",side_effect=finish_loop):
                        with self.assertRaises(WorkerCycleComplete):
                            asyncio.run(worker.main())
        sync.assert_called_once_with()
        tool.assert_awaited_once_with()
        recovery.assert_awaited_once_with()

    def test_builder_page_and_navigation(self):
        view=self.client.get("/workflow-builder")
        self.assertEqual(view.status_code,200)
        self.assertIn("id=\"pipeline\"",view.text)
        self.assertIn("Workflow Builder",self.client.get("/dashboard").text)
        self.assertIn("Workflow Builder",self.client.get("/tool-hub").text)

    def test_browser_script_has_valid_javascript(self):
        """Check the actual single-file builder JavaScript with Node, when available."""
        import re
        import shutil
        import subprocess
        node=shutil.which("node")
        if not node:
            self.skipTest("Node.js not installed")
        html=self.client.get("/workflow-builder").text
        match=re.search(r"<script>(.*?)</script>",html,re.S)
        self.assertIsNotNone(match)
        with tempfile.NamedTemporaryFile(mode="w",suffix=".js",encoding="utf-8") as handle:
            handle.write(match.group(1))
            handle.flush()
            checked=subprocess.run([node,"--check",handle.name],capture_output=True,text=True)
        self.assertEqual(checked.returncode,0,checked.stderr)

    def test_validation_rejects_unreviewed_or_wrong_order_publish(self):
        x=self.client.post("/workspaces/demo/builder/templates",
            json={"name":"Không có QA","steps":[
                {"type":"agent","role":"research","instruction":"Nghiên cứu thị trường có nguồn"},
                {"type":"agent","role":"content","instruction":"Viết bài dựa trên số liệu"},
                {"type":"tool","action":"wordpress.create_post"}]})
        self.assertEqual(x.status_code,422)
        x=self.client.post("/workspaces/demo/builder/templates",
            json={"name":"Tool ở giữa","steps":[
                {"type":"agent","role":"content","instruction":"Viết bài giới thiệu dự án"},
                {"type":"tool","action":"wordpress.create_post"},
                {"type":"agent","role":"quality","instruction":"Duyệt nội dung này"}]})
        self.assertEqual(x.status_code,422)
        x=self.client.post("/workspaces/demo/builder/templates",
            json={"name":"Sai vai trò","steps":[
                {"type":"agent","role":"fake","instruction":"Công việc không xác định"},
                {"type":"agent","role":"quality","instruction":"Kiểm tra lại thông tin"}]})
        self.assertEqual(x.status_code,422)

    def test_sequential_tasks_and_idempotent_run(self):
        tpl=self.template(stages())
        self.assertEqual(len(self.client.get("/workspaces/demo/builder/templates").json()["templates"]),1)
        run=self.launch(tpl["id"])
        self.assertEqual(len(run["tasks"]),3)
        self.assertEqual(run["status"],"running")
        clone=self.launch(tpl["id"])
        self.assertEqual(clone["id"],run["id"])
        self.assertTrue(clone["deduplicated"])
        with core.db() as c:
            rows=[c.execute("SELECT depends_on FROM tasks WHERE id=?",(x["id"],)).fetchone()
                  for x in run["tasks"]]
        self.assertEqual([r["depends_on"] for r in rows],
                         [None,run["tasks"][0]["id"],run["tasks"][1]["id"]])
        self.mark_complete([t["id"] for t in run["tasks"]])
        synced=self.client.post("/builder/runs/"+run["id"]+"/sync")
        self.assertEqual(synced.json()["status"],"completed")

    def test_tool_request_only_after_qa_pass_and_never_auto_execute(self):
        tpl=self.template(stages("wordpress.create_post"))
        run=self.launch(tpl["id"])
        ids=[x["id"] for x in run["tasks"]]
        before=self.client.post("/builder/runs/"+run["id"]+"/sync").json()
        self.assertIsNone(before["tool_job_id"])
        self.assertEqual(self.client.get("/workspaces/demo/tool-jobs").json()["jobs"],[])
        self.mark_complete(ids)
        # Actual QA record from the existing SHA-bound review mechanism.
        created=self.client.post("/builder/runs/"+run["id"]+"/sync")
        self.assertEqual(created.status_code,200,created.text)
        data=created.json()
        self.assertEqual(data["status"],"awaiting_tool_approval")
        job=data["tool"]
        self.assertEqual(job["status"],"awaiting_approval")
        # Reading worker state or running sync again never submits the post.
        self.client.post("/builder/runs/"+run["id"]+"/sync")
        jobs=self.client.get("/workspaces/demo/tool-jobs").json()["jobs"]
        self.assertEqual(len(jobs),1)
        self.assertEqual(jobs[0]["payload"]["content"],"Nội dung do Agent tạo: bài viết dự án theo chứng cứ")
        self.assertEqual(jobs[0]["payload"]["source_task_id"],ids[-2])
        self.assertEqual(jobs[0]["action"],"wordpress.create_post")
        self.assertEqual(self.client.post(f"/tool-jobs/{job['id']}/approve").json()["status"],"approved")
        self.assertEqual(self.client.get("/workspaces/demo/tool-jobs").json()["jobs"][0]["status"],"approved")

    def test_quality_fail_never_generates_tool_job(self):
        tpl=self.template(stages("postiz.create_post"))
        run=self.launch(tpl["id"],idem="qa-failure-123")
        self.mark_complete([t["id"] for t in run["tasks"]],quality="fail")
        synced=self.client.post("/builder/runs/"+run["id"]+"/sync")
        self.assertEqual(synced.status_code,200,synced.text)
        self.assertEqual(synced.json()["status"],"review_required")
        self.assertIsNone(synced.json()["tool_job_id"])
        self.assertEqual(self.client.get("/workspaces/demo/tool-jobs").json()["jobs"],[])

    def test_publisher_must_belong_to_credential_pilot_workspace(self):
        new=self.client.post("/workspaces",json={"id":"other","name":"Khác","industry":"general","budget_usd":2})
        self.assertEqual(new.status_code,201)
        tpl=self.template(stages("wordpress.create_post"),ws="other")
        start=self.client.post(f"/workspaces/other/builder/templates/{tpl['id']}/run",
                               json={"objective":"Đăng nội dung của doanh nghiệp riêng biệt"})
        self.assertEqual(start.status_code,403)
        self.assertEqual(self.client.get("/workspaces/other/builder/runs").json()["runs"],[])

    def test_template_edits_do_not_change_existing_runs(self):
        tpl=self.template(stages())
        run=self.launch(tpl["id"],idem="snapshot-123")
        updated=self.client.put("/workspaces/demo/builder/templates/"+tpl["id"],json={
            "name":"Mẫu sửa mới","steps":[
                {"type":"agent","role":"strategy","instruction":"Xây chiến lược có bằng chứng"},
                {"type":"agent","role":"quality","instruction":"Kiểm tra chiến lược"}]})
        self.assertEqual(updated.status_code,200,updated.text)
        old=self.client.get("/builder/runs/"+run["id"]).json()
        self.assertEqual(len(old["tasks"]),3)
        self.assertEqual(old["definition"]["steps"][0]["role"],"research")

    def test_background_sync_creates_tool_request_not_execution(self):
        tpl=self.template(stages("wordpress.create_post"))
        run=self.launch(tpl["id"],idem="worker-sync-123")
        self.mark_complete([t["id"] for t in run["tasks"]])
        builder.sync_ready_builder_runs()
        job=self.client.get("/builder/runs/"+run["id"]).json()["tool"]
        self.assertEqual(job["status"],"awaiting_approval")


if __name__=="__main__":
    unittest.main()
