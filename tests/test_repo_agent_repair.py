"""Real HTTP GitHub write-adapter contract via mocked network, real SQLite/ASGI."""
from __future__ import annotations

import base64
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

import app
import core
import repo_agent_bindings as bindings
import repo_agent_repair as repair


class RepairPRTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="lhm_patch_")
        self.addCleanup(self.tmp.cleanup)
        before=core.DATABASE
        core.DATABASE=Path(self.tmp.name)/"repair.sqlite3"
        self.addCleanup(setattr,core,"DATABASE",before)
        core.setup()
        repair.prepare_db()
        self.env=patch.dict(os.environ,{
            "LHM_ADMIN_TOKEN":"","LHM_GITHUB_READ_TOKEN":"read-test-token",
            "LHM_GITHUB_WRITE_TOKEN":"write-test-token",
            "LHM_OPENROUTER_FREE_ONLY":"1","OPENROUTER_API_KEY":"offline-key"})
        self.env.start();self.addCleanup(self.env.stop)
        self.client=TestClient(app.app)
        with core.db() as c:
            task=app.task_record(c,"demo","quality","Phân tích lỗi build trong CI",
                                 "CI failed evidence",model="openrouter/free")
            self.taskid=task["id"]
            c.execute("UPDATE tasks SET status='completed',result='Bản nháp: sửa lỗi file mẫu' WHERE id=?",
                      (self.taskid,))
            self.diag_id=core.new_id()
            c.execute("""INSERT INTO repo_agent_diagnoses
                (id,workspace_id,agent_id,repository,run_id,task_id,created_at)
                VALUES(?,?,?,?,?,?,?)""",
                (self.diag_id,"demo","agent-cnlg","capquangfptminhlh/cnlg",
                 9966,self.taskid,core.now()))
        self.calls=[]
        self.current_head="a"*40
        self.file_sha="b"*40
        self.pr_error=False
        self.source='print("old")\n'

    def handle(self,req):
        self.calls.append((req.method,req.url.path,req))
        assert req.url.host=="api.github.com",req.url
        assert req.url.path.startswith("/repos/capquangfptminhlh/cnlg/")
        if req.url.path.endswith("/branches/main"):
            return httpx.Response(200,json={"commit":{"sha":self.current_head}})
        if "/contents/" in req.url.path:
            if req.method=="GET":
                return httpx.Response(200,json={"type":"file","encoding":"base64",
                    "sha":self.file_sha,
                    "content":base64.b64encode(self.source.encode()).decode()})
            if req.method=="PUT":
                body=__import__("json").loads(req.content)
                self.assertEqual(body["sha"],self.file_sha)
                self.assertTrue(body["branch"].startswith("agent-repair/agent-cnlg/"))
                self.assertEqual(base64.b64decode(body["content"]).decode(),
                                 'print("fixed")\n')
                self.assertEqual(req.headers.get("authorization"),"Bearer write-test-token")
                return httpx.Response(200,json={"commit":{"sha":"c"*40}})
        if req.url.path.endswith("/git/refs"):
            body=__import__("json").loads(req.content)
            self.assertEqual(body["sha"],"a"*40)
            return httpx.Response(201,json={"ref":body["ref"]})
        if req.url.path.endswith("/pulls"):
            if self.pr_error:
                return httpx.Response(502,json={"message":"remote uncertain"})
            body=__import__("json").loads(req.content)
            self.assertTrue(body["draft"])
            self.assertEqual(body["base"],"main")
            self.assertTrue(body["head"].startswith("agent-repair/agent-cnlg/"))
            self.assertEqual(req.headers.get("authorization"),"Bearer write-test-token")
            return httpx.Response(201,json={"number":42,"html_url":"https://evil.example/pr"})
        self.fail("Unexpected GitHub request "+req.method+" "+req.url.path)

    def transport(self):
        original=httpx.AsyncClient
        return patch.object(repair.httpx,"AsyncClient",
                            side_effect=lambda *a,**kw:original(
                                *a,transport=httpx.MockTransport(self.handle),**kw))

    def approve(self,repair_id):
        with patch.dict(os.environ,{"LHM_ADMIN_TOKEN":"owner-ci-admin"}):
            return self.client.post(
                "/workspaces/demo/repo-agents/agent-cnlg/repairs/"+repair_id+"/approve",
                headers={"Authorization":"Bearer owner-ci-admin"})

    def payload(self,key="repair-10001",content='print("fixed")\n'):
        return {"workspace":"demo","diagnosis_id":self.diag_id,
                "path":"src/fix.py","new_content":content,
                "explanation":"Fix reproducible CI issue reported by build job",
                "idempotency_key":key}

    def stage(self):
        with self.transport():
            r=self.client.post("/workspaces/demo/repo-agents/agent-cnlg/repairs",
                               json=self.payload())
        self.assertEqual(r.status_code,201,r.text)
        self.assertEqual(r.json()["status"],"awaiting_approval")
        return r.json()

    def test_owner_preview_stage_and_real_draft_pr_with_no_merge(self):
        with self.transport():
            source=self.client.get("/repo-agents/agent-cnlg/repair-source",
                                   params={"path":"src/fix.py"})
        self.assertEqual(source.status_code,200,source.text)
        self.assertEqual(source.json()["content"],self.source)
        staged=self.stage()
        self.assertIn('print("fixed")',staged["diff_preview"])
        self.assertEqual(staged["base_head_sha"],"a"*40)
        self.assertFalse(staged["externally_written"])
        self.assertTrue(staged["human_approval_required"])
        self.assertFalse(staged["automatic_merge"])
        self.assertFalse(any(method!="GET" for method,_,_ in self.calls))
        with self.transport():
            posted=self.approve(staged["id"])
        self.assertEqual(posted.status_code,200,posted.text)
        body=posted.json()
        self.assertEqual(body["status"],"pr_created")
        self.assertEqual(body["pull_request_url"],
                         "https://github.com/capquangfptminhlh/cnlg/pull/42")
        self.assertEqual([m for m,_,_ in self.calls if m!="GET"],
                         ["POST","PUT","POST"])
        again=self.approve(staged["id"])
        self.assertEqual(again.status_code,409)
        self.assertEqual(len(self.client.get("/workspaces/demo/repo-agents/agent-cnlg/repairs").
                             json()["repairs"]),1)

    def test_local_service_without_admin_auth_must_not_write_github(self):
        staged=self.stage()
        response=self.client.post(
            "/workspaces/demo/repo-agents/agent-cnlg/repairs/"+staged["id"]+"/approve")
        self.assertEqual(response.status_code,403)
        self.assertEqual(self.client.get(
            "/workspaces/demo/repo-agents/agent-cnlg/repairs/"+staged["id"]
        ).json()["status"],"awaiting_approval")

    def test_no_write_key_does_not_change_awaiting_approval(self):
        staged=self.stage()
        with patch.dict(os.environ,{"LHM_GITHUB_WRITE_TOKEN":""}):
            r=self.approve(staged["id"])
        self.assertEqual(r.status_code,424)
        stored=self.client.get("/workspaces/demo/repo-agents/agent-cnlg/repairs/"+
                               staged["id"]).json()
        self.assertEqual(stored["status"],"awaiting_approval")
        self.assertFalse(stored["externally_written"])

    def test_stale_main_ref_fails_before_git_write(self):
        staged=self.stage()
        self.calls.clear()
        self.current_head="d"*40
        with self.transport():
            result=self.approve(staged["id"])
        self.assertEqual(result.status_code,409)
        stored=self.client.get("/workspaces/demo/repo-agents/agent-cnlg/repairs/"+
                               staged["id"]).json()
        self.assertEqual(stored["status"],"stale")
        self.assertFalse(any(method!="GET" for method,_,_ in self.calls))

    def test_upstream_pr_failure_needs_reconciliation_not_automatic_retry(self):
        staged=self.stage()
        self.pr_error=True
        with self.transport():
            failed=self.approve(staged["id"])
        self.assertIn(failed.status_code,(424,502,503))
        stored=self.client.get("/workspaces/demo/repo-agents/agent-cnlg/repairs/"+
                               staged["id"]).json()
        self.assertEqual(stored["status"],"needs_reconciliation")
        self.assertTrue(stored["externally_written"])
        self.assertIsNone(stored["pull_request_number"])
        before=len(self.calls)
        with self.transport():
            again=self.approve(staged["id"])
        self.assertEqual(again.status_code,409)
        self.assertEqual(len(self.calls),before)

    def test_duplicate_proposal_different_data_and_owner_reject(self):
        staged=self.stage()
        with self.transport():
            idem=self.client.post("/workspaces/demo/repo-agents/agent-cnlg/repairs",
                                  json=self.payload())
        self.assertEqual(idem.status_code,201)
        self.assertTrue(idem.json()["deduplicated"])
        with self.transport():
            different=self.client.post("/workspaces/demo/repo-agents/agent-cnlg/repairs",
                        json=self.payload(content="different code"))
        self.assertEqual(different.status_code,409)
        rejected=self.client.post("/workspaces/demo/repo-agents/agent-cnlg/repairs/"+
                                  staged["id"]+"/reject")
        self.assertEqual(rejected.status_code,200)
        self.assertEqual(rejected.json()["status"],"rejected")
        self.assertEqual(self.approve(staged["id"]).status_code,409)

    def test_wrong_workspace_agent_and_unsafe_path_never_read_github(self):
        for path in ("../secrets.py",".github/workflows/ci.yml",
                     "src/../../escape.js","src/token_config.py",
                     "src/secret.json","C:\\Windows\\test.py",
                     "src/evil.exe","src//double.py"):
            res=self.client.post("/workspaces/demo/repo-agents/agent-cnlg/repairs",
                                 json=self.payload(key="unsafe-path-0001")|{"path":path})
            self.assertEqual(res.status_code,422,(path,res.text))
        res=self.client.post("/workspaces/demo/repo-agents/agent-cnlg/repairs",
                             json=self.payload()|{"workspace":"wrong"})
        self.assertEqual(res.status_code,422)
        res=self.client.post("/workspaces/other/repo-agents/agent-cnlg/repairs",
                             json=self.payload())
        self.assertEqual(res.status_code,422)
        res=self.client.post("/workspaces/demo/repo-agents/agent-autotax/repairs",
                             json=self.payload())
        self.assertEqual(res.status_code,404)
        self.assertEqual(self.calls,[])

    def test_diagnosis_must_be_finished_and_in_same_workspace(self):
        with core.db() as c:
            c.execute("UPDATE tasks SET status='queued',result=NULL WHERE id=?",(self.taskid,))
        with self.transport():
            result=self.client.post("/workspaces/demo/repo-agents/agent-cnlg/repairs",
                                    json=self.payload())
        self.assertEqual(result.status_code,409)
        self.assertEqual(self.calls,[])


if __name__=="__main__":
    unittest.main()
