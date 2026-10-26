"""Regression checks for publication safety; providers are never contacted."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import app
import core
import publication_gate as gate
import tool_hub


class PublicationSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db = core.DATABASE
        core.DATABASE = Path(self.tmp.name) / "safety.sqlite3"
        core.setup()
        tool_hub.prepare_db()
        self.env = patch.dict(os.environ, {"LHM_ADMIN_TOKEN": "",
                                            "LHM_TOOLS_WORKSPACE": "demo"})
        self.env.start()
        self.client = TestClient(app.app)

    def tearDown(self):
        self.env.stop()
        core.DATABASE = self.original_db
        self.tmp.cleanup()

    def done(self, role: str, content: str, depends_on: str | None = None) -> str:
        with core.db() as c:
            item = app.task_record(c, "demo", role, "Kiểm tra nội dung theo yêu cầu",
                                   "", depends_on=depends_on)
            c.execute("UPDATE tasks SET status='completed',result=? WHERE id=?",
                      (content, item["id"]))
            if role in gate.REVIEW_ROLES:
                gate.record_review(c, {"id": item["id"], "workspace_id": "demo",
                                       "role": role, "depends_on": depends_on}, content)
        return item["id"]

    def tool(self, source: str, content: str, idem: str):
        return self.client.post("/workspaces/demo/tool-jobs", json={
            "action": "wordpress.create_post",
            "idempotency_key": idem,
            "payload": {"title": "Thông tin kiểm chứng", "content": content,
                        "status": "publish", "source_task_id": source}})

    def test_source_link_requires_positive_qa(self):
        source = self.done("content", "Bản nháp cần kiểm tra")
        self.assertEqual(self.tool(source, "Bản nháp cần kiểm tra", "nogate001").status_code, 409)
        self.assertEqual(self.client.post("/workspaces/demo/tool-jobs/from-agent",
                        json={"source_task_id":source, "action":"wordpress.create_post",
                              "idempotency_key":"nogate002", "title":"Bài viết"}).status_code, 409)

    def test_qa_fail_blocks_direct_and_dependent(self):
        source = self.done("content", "Nội dung sai cần sửa")
        reviewer = self.done("quality", '{"verdict":"fail","reason":"Dữ liệu sai"}', source)
        self.assertEqual(self.tool(source, "Nội dung sai cần sửa", "failgate01").status_code, 409)
        with core.db() as c:
            child = app.task_record(c, "demo", "ads", "Chuẩn bị quảng cáo", "",
                                    depends_on=reviewer)["id"]
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY":"fake"}):
            out = app.claim_task(child)
        self.assertTrue(out["dependency_blocked"])
        self.assertEqual(self.client.get(f"/tasks/{child}").json()["status"], "blocked_dependency")

    def test_latest_compliance_fail_cannot_be_overridden_by_quality_pass(self):
        source_text = "Thông tin bất động sản chưa xác nhận"
        source = self.done("content", source_text)
        quality = self.done("quality", '{"verdict":"pass"}', source)
        self.done("compliance", '{"verdict":"fail"}', quality)
        self.assertEqual(self.tool(source, source_text, "compfail01").status_code, 409)

    def test_qa_pass_allows_job_but_content_change_revokes_execution(self):
        source_text = "Nội dung V1 được duyệt"
        source = self.done("content", source_text)
        self.done("quality", '{"verdict":"pass"}', source)
        response = self.tool(source, source_text, "hashbound01")
        self.assertEqual(response.status_code, 201, response.text)
        job = response.json()["id"]
        self.assertEqual(self.client.post(f"/tool-jobs/{job}/approve").status_code, 200)
        with core.db() as c:
            c.execute("UPDATE tasks SET result=? WHERE id=?", ("Nội dung V2 đã đổi", source))
        with patch.object(tool_hub, "dispatch_tool", new_callable=AsyncMock) as remote:
            res = self.client.post(f"/tool-jobs/{job}/execute")
            remote.assert_not_awaited()
        self.assertEqual(res.status_code, 409)
        self.assertEqual(self.tool(source, "Nội dung V2 đã đổi", "hashbound02").status_code, 409)

    def test_source_payload_must_equal_reviewed_source(self):
        source = self.done("content", "Văn bản V1")
        self.done("quality", '{"verdict":"pass"}', source)
        self.assertEqual(self.tool(source,"Văn bản đã bị sửa", "tamper001").status_code, 409)

    def test_approved_payload_and_target_are_immutable(self):
        response = self.client.post("/workspaces/demo/tool-jobs",json={
            "action": "wordpress.create_post", "idempotency_key":"targethash01",
            "payload":{"title":"A", "content":"Bài tự viết", "status":"draft"}})
        job = response.json()["id"]
        self.assertEqual(self.client.post(f"/tool-jobs/{job}/approve").status_code,200)
        with core.db() as c:
            c.execute("UPDATE tool_jobs SET payload_json=? WHERE id=?",
                      (json.dumps({"title":"A","content":"Bài tự viết","status":"publish"}),job))
        with patch.object(tool_hub,"dispatch_tool",new_callable=AsyncMock) as remote:
            reply=self.client.post(f"/tool-jobs/{job}/execute")
            remote.assert_not_awaited()
        self.assertEqual(reply.status_code,409)

    def test_qa_new_fail_after_approval_revokes_job(self):
        text = "Bài đăng đang chờ thực thi"
        source = self.done("content", text)
        self.done("quality", '{"verdict":"pass"}', source)
        job = self.tool(source,text,"recheck01").json()["id"]
        self.assertEqual(self.client.post(f"/tool-jobs/{job}/approve").status_code,200)
        self.done("quality", '{"verdict":"fail"}', source)
        with patch.object(tool_hub,"dispatch_tool",new_callable=AsyncMock) as remote:
            r=self.client.post(f"/tool-jobs/{job}/execute")
            remote.assert_not_awaited()
        self.assertEqual(r.status_code,409)

    def test_full_json_handoff_includes_end_marker(self):
        body = {"copy":"X"*4300, "handoff":{"final_marker":"END_IS_REVIEWED"}}
        rendered = json.dumps(body)
        predecessor = self.done("content", rendered)
        with core.db() as c:
            child = app.task_record(c,"demo","quality","Kiểm tra cả bài dài","",
                                    depends_on=predecessor)["id"]
        with patch.dict(os.environ,{"DEEPSEEK_API_KEY":"fake"}):
            claimed = app.claim_task(child)
        self.assertIn("END_IS_REVIEWED", claimed["context"])
        self.assertEqual(json.loads(claimed["context"].split("ĐẦU RA GIAI ĐOẠN TRƯỚC:\n",1)[1]), body)

    def test_excessive_handoff_blocks_instead_of_truncating(self):
        predecessor = self.done("content","X"*(gate.MAX_HANDOFF_CHARS+20))
        with core.db() as c:
            child=app.task_record(c,"demo","quality","Kiểm tra đầu ra rất dài","",
                                  depends_on=predecessor)["id"]
        with patch.dict(os.environ,{"DEEPSEEK_API_KEY":"fake"}):
            result=app.claim_task(child)
        self.assertTrue(result["dependency_blocked"])
        self.assertEqual(self.client.get(f"/tasks/{child}").json()["status"],"blocked_dependency")

    def test_output_pii_redacted_before_db_and_api(self):
        created = self.client.post("/workspaces/demo/tasks", json={
            "role":"content", "instruction":"Soạn bài cho dự án"}).json()["id"]
        raw = "Liên hệ 0901234567 hoặc sample.person@example.com để nhận báo giá"
        async def fake(*args, **kwargs):
            return {"choices":[{"message":{"content":raw}}],
                    "usage":{"prompt_tokens":45,"completion_tokens":25}}
        with patch.dict(os.environ,{"DEEPSEEK_API_KEY":"fake"}):
            with patch.object(app,"deepseek_request",side_effect=fake):
                reply=self.client.post(f"/tasks/{created}/run")
        self.assertEqual(reply.status_code,200,reply.text)
        self.assertEqual(reply.json()["status"],"failed")
        stored=json.dumps(self.client.get(f"/tasks/{created}").json(),ensure_ascii=False)
        self.assertNotIn("0901234567",stored)
        self.assertNotIn("sample.person@example.com",stored)
        self.assertIn("[REDACTED_",stored)

    def test_legitimate_crm_contacts_are_not_silently_erased(self):
        text="Địa chỉ công khai của doanh nghiệp: info@example.com"
        clean,blocked=gate.scrub_output("crm",text)
        self.assertFalse(blocked)
        self.assertEqual(clean,text)

    def test_oversize_from_agent_rejected_not_truncated(self):
        source = self.done("content","V"*15001)
        self.done("quality",'{"verdict":"pass"}',source)
        response = self.client.post("/workspaces/demo/tool-jobs/from-agent",json={
            "source_task_id":source,"action":"postiz.create_post",
            "idempotency_key":"nosilenttrunc01","mode":"now",
            "channel_ids":["channel-1"]})
        self.assertEqual(response.status_code,422)


if __name__ == "__main__":
    unittest.main()
