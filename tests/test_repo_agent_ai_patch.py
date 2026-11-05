"""Real worker + SQLite lifecycle of AI-generated, review-gated CI patch proposals.

Only LLM and GitHub HTTP reads are mocked. Never contacts production providers.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import app
import core
import repo_agent_ai_patch as ai
import repo_agent_repair as repair
import worker


class AIPatchTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix="lhm_ai_patch_")
        self.addCleanup(temp.cleanup)
        original=core.DATABASE
        core.DATABASE=Path(temp.name)/"tasks.sqlite3"
        self.addCleanup(setattr,core,"DATABASE",original)
        core.setup()
        ai.prepare_db()
        env=patch.dict(os.environ,{"LHM_ADMIN_TOKEN":"",
            "LHM_OPENROUTER_FREE_ONLY":"1","OPENROUTER_API_KEY":"offline-ci-key",
            "LHM_GITHUB_READ_TOKEN":"mock-read"})
        env.start();self.addCleanup(env.stop)
        self.client=TestClient(app.app)
        self.file='def calc(x):\n    return x + 1\n'
        self.mainsha="a"*40
        self.blobsha="b"*40
        self.original_payload=self.source()
        with core.db() as c:
            t=app.task_record(c,"demo","quality","Diagnose CI source failure",
                              "GitHub CI failed",model="openrouter/free")
            self.source_task=t["id"]
            c.execute("UPDATE tasks SET status='completed',result='Test fail: calc must return x + 2' WHERE id=?",
                      (t["id"],))
            self.diag_id=core.new_id()
            c.execute("""INSERT INTO repo_agent_diagnoses
                (id,workspace_id,agent_id,repository,run_id,task_id,created_at)
                VALUES(?,?,?,?,?,?,?)""",(self.diag_id,"demo","agent-cnlg",
                "capquangfptminhlh/cnlg",123456,t["id"],core.now()))
        self.patch={"search":"    return x + 1\n",
                    "replace":"    return x + 2\n",
                    "explanation":"Sửa kết quả theo kiểm thử đã thất bại trên main",
                    "tests":"Chạy unit tests của hàm calc trên CI"}
        self.response=self.reply(self.patch)

    def source(self):
        return {"agent_id":"agent-cnlg","repository":"capquangfptminhlh/cnlg",
                "head_sha":self.mainsha,"blob_sha":self.blobsha,
                "content":self.file,"path":"src/calc.py"}

    def reply(self,body):
        return {"choices":[{"message":{"content":json.dumps(body,ensure_ascii=False)}}],
                "usage":{"prompt_tokens":20,"completion_tokens":25,"cost":0}}

    def payload(self,key="ai-key-20261028",path="src/calc.py"):
        return {"workspace":"demo","diagnosis_id":self.diag_id,
                "path":path,"idempotency_key":key}

    def enqueue(self):
        with patch.object(ai,"get_repair_source",new=AsyncMock(return_value=self.source())):
            response=self.client.post("/workspaces/demo/repo-agents/agent-cnlg/ai-suggestions",
                                      json=self.payload())
        self.assertEqual(response.status_code,201,response.text)
        return response.json()

    def complete(self,task_id,reply=None):
        with core.db() as c:
            row=c.execute("SELECT id,role,workspace_id,model FROM tasks WHERE id=?",
                          (task_id,)).fetchone()
        model=AsyncMock(return_value=reply or self.response)
        with patch.object(app,"deepseek_request",new=model):
            self.assertEqual(asyncio.run(worker.dispatch_agent_batch([row])),1)
        self.assertEqual(model.await_count,1)

    def base(self):
        return "/workspaces/demo/repo-agents/agent-cnlg/ai-suggestions"

    def test_real_worker_generates_single_snippet_patch_with_no_github_writes(self):
        initial=self.enqueue()
        self.assertEqual(initial["task_status"],"queued")
        self.assertFalse(initial["suggestion_valid"])
        self.assertFalse(initial["git_writes"])
        self.complete(initial["task_id"])
        d=self.client.get(self.base()+"/"+initial["id"]).json()
        self.assertEqual(d["task_status"],"completed")
        self.assertTrue(d["suggestion_valid"])
        self.assertTrue(d["can_stage"])
        self.assertIn("return x + 2",d["diff_preview"])
        self.assertFalse(d["auto_merge"])
        self.assertIsNone(d["staged_repair_id"])
        with core.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM repo_agent_repairs").fetchone()[0],0)

    def test_stage_creates_reviewable_repair_without_external_github_actions(self):
        sent=self.enqueue()
        self.complete(sent["task_id"])
        with patch.object(ai,"get_repair_source",new=AsyncMock(return_value=self.source())) as reader:
            staged=self.client.post(self.base()+"/"+sent["id"]+"/stage")
        self.assertEqual(staged.status_code,200,staged.text)
        patch_record=staged.json()
        self.assertEqual(reader.await_count,1)
        self.assertEqual(patch_record["status"],"awaiting_approval")
        self.assertEqual(patch_record["new_content"],"def calc(x):\n    return x + 2\n")
        self.assertEqual(patch_record["source_blob_sha"],"b"*40)
        self.assertFalse(patch_record["externally_written"])
        self.assertTrue(patch_record["human_approval_required"])
        self.assertIsNone(patch_record["pull_request_url"])
        again=self.client.post(self.base()+"/"+sent["id"]+"/stage")
        self.assertEqual(again.status_code,200)
        self.assertTrue(again.json()["deduplicated"])
        self.assertEqual(again.json()["id"],patch_record["id"])
        self.assertEqual(self.client.get(self.base()).json()["suggestions"][0]["staged_repair_id"],
                         patch_record["id"])
        with core.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM repo_agent_repairs").fetchone()[0],1)

    def test_source_sha_change_blocks_staging_without_leaving_partial_repair(self):
        sent=self.enqueue()
        self.complete(sent["task_id"])
        current={**self.source(),"head_sha":"c"*40}
        with patch.object(ai,"get_repair_source",new=AsyncMock(return_value=current)):
            fail=self.client.post(self.base()+"/"+sent["id"]+"/stage")
        self.assertEqual(fail.status_code,409,fail.text)
        with core.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM repo_agent_repairs").fetchone()[0],0)
        self.assertIsNone(self.client.get(self.base()+"/"+sent["id"]).json()["staged_repair_id"])

    def test_invalid_llm_json_and_ambiguous_search_fail_closed(self):
        for name,answer in (
            ("invalid",{"choices":[{"message":{"content":"Not JSON: solved!"}}],
                        "usage":{"prompt_tokens":5,"completion_tokens":5,"cost":0}}),
            ("ambiguous",self.reply({**self.patch,"search":"nonexistent content"})),
            ("keys",self.reply({**self.patch,"gh_token":"fake"})),
        ):
            with self.subTest(case=name):
                d=self.enqueue() if name=="invalid" else self._enqueue_new(name)
                self.complete(d["task_id"],reply=answer)
                stored=self.client.get(self.base()+"/"+d["id"]).json()
                self.assertFalse(stored["suggestion_valid"])
                self.assertFalse(stored["can_stage"])
                self.assertEqual(self.client.post(self.base()+"/"+d["id"]+"/stage").status_code,409)
        with core.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM repo_agent_repairs").fetchone()[0],0)

    def _enqueue_new(self,key):
        with patch.object(ai,"get_repair_source",new=AsyncMock(return_value=self.source())):
            response=self.client.post(self.base(),json=self.payload(key="test-"+key+"-key"))
        self.assertEqual(response.status_code,201,response.text)
        return response.json()

    def test_same_intent_idempotent_and_changed_intent_conflicts(self):
        first=self.enqueue()
        with patch.object(ai,"get_repair_source",new=AsyncMock()) as read:
            second=self.client.post(self.base(),json=self.payload())
            conflict=self.client.post(self.base(),json=self.payload(path="src/other.py"))
        self.assertEqual(second.status_code,201,second.text)
        self.assertTrue(second.json()["deduplicated"])
        self.assertEqual(second.json()["id"],first["id"])
        self.assertEqual(conflict.status_code,409)
        read.assert_not_awaited()
        with core.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM repo_agent_ai_suggestions").fetchone()[0],1)

    def test_secrets_binary_large_file_unsafe_paths_rejected_before_worker(self):
        for path in (".github/workflows/ci.yml","../secret.py","src/password.py","src/run.exe"):
            result=self.client.post(self.base(),json=self.payload(key="bad-path-2026",path=path))
            self.assertEqual(result.status_code,422,(path,result.text))
        unsafe={**self.source(),"content":'OPENROUTER_API_KEY="sk-proj-not-allowed"\n'}
        with patch.object(ai,"get_repair_source",new=AsyncMock(return_value=unsafe)):
            result=self.client.post(self.base(),json=self.payload())
        self.assertEqual(result.status_code,422,result.text)
        oversized={**self.source(),"content":"x"*9000}
        with patch.object(ai,"get_repair_source",new=AsyncMock(return_value=oversized)):
            result=self.client.post(self.base(),json=self.payload())
        self.assertEqual(result.status_code,422)
        with core.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM repo_agent_ai_suggestions").fetchone()[0],0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM tasks").fetchone()[0],1)

    def test_cross_workspace_wrong_diagnosis_and_uncompleted_blocked(self):
        result=self.client.post(self.base(),json={**self.payload(),"workspace":"other"})
        self.assertEqual(result.status_code,422)
        with patch.object(ai,"get_repair_source",new=AsyncMock()) as reader:
            result=self.client.post(self.base(),json={**self.payload(),
                              "diagnosis_id":"00000000-1111-2222-3333-444444444444"})
        self.assertEqual(result.status_code,404)
        reader.assert_not_awaited()
        with core.db() as c:
            c.execute("UPDATE tasks SET status='queued',result=NULL WHERE id=?",
                      (self.source_task,))
        with patch.object(ai,"get_repair_source",new=AsyncMock()) as reader:
            result=self.client.post(self.base(),json=self.payload())
        self.assertEqual(result.status_code,409)
        reader.assert_not_awaited()

    def test_ai_patch_ui_javascript_check(self):
        html=self.client.get("/repo-agents/ui").text
        for txt in ('id="aiSuggest"','id="aiRead"','id="aiStage"',
                    'function presentAI(d)','/ai-suggestions'):
            self.assertIn(txt,html)
        node=shutil.which("node")
        if not node:self.skipTest("Node missing")
        script=re.search(r"<script>(.*?)</script>",html,re.S)
        self.assertIsNotNone(script)
        with tempfile.NamedTemporaryFile(mode="w",suffix=".js",encoding="utf8") as tmp:
            tmp.write(script.group(1));tmp.flush()
            result=subprocess.run([node,"--check",tmp.name],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)


if __name__=="__main__":
    unittest.main()
