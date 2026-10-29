"""Live ASGI/SQLite/Worker tests; GitHub HTTP and free AI are mocked."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch

import httpx
from fastapi.testclient import TestClient

import app
import core
import worker
import repo_agent_pr_revision as revisions


class SamePRRevisionTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        before=core.DATABASE
        core.DATABASE=Path(tmp.name)/"revision.sqlite3"
        self.addCleanup(setattr,core,"DATABASE",before)
        core.setup()
        revisions.prepare_db()
        self.env=patch.dict(os.environ,{
            "LHM_ADMIN_TOKEN":"",
            "LHM_OPENROUTER_FREE_ONLY":"1",
            "LHM_GITHUB_READ_TOKEN":"read-only-test",
            "LHM_GITHUB_WRITE_TOKEN":"write-test",
            "OPENROUTER_API_KEY":"offline-model-key"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.client=TestClient(app.app)
        self.repo="capquangfptminhlh/cnlg"
        self.branch="agent-repair/agent-cnlg/abc123"
        self.sha="a"*40
        self.blob="b"*40
        self.source='def calc(x):\n    return x + 1\n'
        self.check_result="failure"
        self.pr_draft=True
        self.pr_open=True
        self.http_calls=[]
        self.put_fail=False
        self.repair_id=core.new_id()
        with core.db() as c:
            d=app.task_record(c,"demo","quality","CI diagnostic",
                              "job failing",model="openrouter/free")
            c.execute("UPDATE tasks SET status='completed',result='Prior main CI error' WHERE id=?",
                      (d["id"],))
            diag_id=core.new_id()
            c.execute("""INSERT INTO repo_agent_diagnoses
             (id,workspace_id,agent_id,repository,run_id,task_id,created_at)
             VALUES(?,?,?,?,?,?,?)""",
             (diag_id,"demo","agent-cnlg",self.repo,661,d["id"],core.now()))
            c.execute("""INSERT INTO repo_agent_repairs
             (id,workspace_id,agent_id,repository,diagnosis_id,path,
              base_head_sha,source_blob_sha,new_content,diff_preview,
              explanation,idempotency_key,status,branch,pr_number,created_at,updated_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
             (self.repair_id,"demo","agent-cnlg",self.repo,diag_id,
              "src/calc.py","c"*40,"d"*40,self.source,"-old\n+new",
              "Fix initial CI","repair-key-1234","pr_created",self.branch,
              77,core.now(),core.now()))
            triage_task=app.task_record(c,"demo","quality","Analyze PR failed CI",
                                        "Windows error",model="openrouter/free")
            c.execute("UPDATE tasks SET status='completed',result='Windows check failed; inspect calc logic' WHERE id=?",
                      (triage_task["id"],))
            self.triage_id=core.new_id()
            c.execute("""INSERT INTO repo_agent_pr_triages
              (id,workspace_id,agent_id,repository,repair_id,pr_number,
               head_sha,task_id,observed_evidence,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
              (self.triage_id,"demo","agent-cnlg",self.repo,self.repair_id,77,
               self.sha,triage_task["id"],'{"verdict":"failed_observed"}',core.now()))
        self.valid={"search":"    return x + 1\n",
                    "replace":"    return x + 2\n",
                    "explanation":"Sửa phép cộng sai khiến Windows CI thất bại",
                    "tests":"Cần chạy unit test calc và Windows CI trên PR mới"}

    def handle(self,req):
        self.http_calls.append((req.method,req.url.path,req))
        self.assertEqual(req.url.host,"api.github.com")
        self.assertTrue(req.url.path.startswith("/repos/"+self.repo+"/"))
        if req.url.path.endswith("/pulls/77"):
            self.assertEqual(req.method,"GET")
            return httpx.Response(200,json={"number":77,
                "state":"open" if self.pr_open else "closed","merged_at":None,
                "draft":self.pr_draft,
                "head":{"sha":self.sha,"ref":self.branch,
                        "repo":{"full_name":self.repo}},
                "base":{"ref":"main","repo":{"full_name":self.repo}}})
        if req.url.path.endswith("/commits/"+self.sha+"/check-runs"):
            return httpx.Response(200,json={"total_count":2,"check_runs":[
                {"name":"Linux","status":"completed","conclusion":"success"},
                {"name":"Windows","status":"completed","conclusion":self.check_result}
            ]})
        if req.url.path.endswith("/commits/"+self.sha+"/status"):
            return httpx.Response(200,json={"statuses":[]})
        if req.url.path.endswith("/contents/src/calc.py"):
            if req.method=="GET":
                self.assertEqual(req.url.params.get("ref"),self.sha)
                return httpx.Response(200,json={
                    "type":"file","encoding":"base64","sha":self.blob,
                    "content":base64.b64encode(self.source.encode()).decode()})
            if req.method=="PUT":
                self.assertEqual(req.headers.get("authorization"),"Bearer write-test")
                payload=json.loads(req.content)
                self.assertEqual(payload["branch"],self.branch)
                self.assertEqual(payload["sha"],self.blob)
                self.assertEqual(base64.b64decode(payload["content"]).decode(),
                                 'def calc(x):\n    return x + 2\n')
                if self.put_fail:
                    return httpx.Response(502,json={"message":"uncertain commit"})
                return httpx.Response(200,json={"commit":{"sha":"e"*40}})
        self.fail("Unexpected GitHub request "+req.method+" "+str(req.url))

    def mocked_http(self):
        original=httpx.AsyncClient
        return patch.object(revisions.httpx,"AsyncClient",side_effect=lambda *a,**kw:
                    original(*a,transport=httpx.MockTransport(self.handle),**kw))

    def endpoint(self):
        return "/workspaces/demo/repo-agents/agent-cnlg/repairs/"+self.repair_id

    def propose(self):
        with self.mocked_http():
            r=self.client.post(self.endpoint()+"/revisions",json={
                "triage_id":self.triage_id,"idempotency_key":"revision-001234"})
        self.assertEqual(r.status_code,201,r.text)
        return r.json()

    def complete(self,record,raw=None):
        with core.db() as c:
            row=c.execute("SELECT * FROM tasks WHERE id=?",(record["task_id"],)).fetchone()
        content=raw if raw is not None else json.dumps(self.valid,ensure_ascii=False)
        fake={"choices":[{"message":{"content":content}}],
              "usage":{"prompt_tokens":30,"completion_tokens":30,"cost":0}}
        model=AsyncMock(return_value=fake)
        with patch.object(app,"deepseek_request",new=model):
            self.assertEqual(asyncio.run(worker.dispatch_agent_batch([row])),1)
        model.assert_awaited_once()

    def owner_approve(self,revision_id):
        with patch.dict(os.environ,{"LHM_ADMIN_TOKEN":"owner-ci-token"}):
            return self.client.post(self.endpoint()+"/revisions/"+revision_id+"/approve",
                 headers={"Authorization":"Bearer owner-ci-token"})

    def test_real_worker_proposes_same_pr_diff_owner_creates_single_commit(self):
        initial=self.propose()
        self.assertEqual(initial["task_status"],"queued")
        self.assertFalse(initial["can_approve"])
        self.assertFalse(initial["automatic_pr_creation"])
        self.assertEqual(initial["head_sha"],"a"*40)
        self.assertEqual(len(self.http_calls),4)
        self.assertTrue(all(m=="GET" for m,_,_ in self.http_calls))
        self.complete(initial)
        draft=self.client.get(self.endpoint()+"/revisions/"+initial["id"])
        self.assertEqual(draft.status_code,200,draft.text)
        self.assertTrue(draft.json()["can_approve"])
        self.assertIn("return x + 2",draft.json()["diff_preview"])
        self.http_calls.clear()
        with self.mocked_http():
            result=self.owner_approve(initial["id"])
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()["status"],"committed")
        self.assertEqual(result.json()["commit_sha"],"e"*40)
        self.assertEqual(result.json()["pr_number"],77)
        self.assertEqual([m for m,_,_ in self.http_calls if m!="GET"],["PUT"])
        with self.mocked_http():
            again=self.owner_approve(initial["id"])
        self.assertEqual(again.status_code,409)
        self.assertEqual([m for m,_,_ in self.http_calls if m!="GET"],["PUT"])

    def test_duplicate_request_does_not_queue_new_task(self):
        x=self.propose()
        with self.mocked_http():
            second=self.client.post(self.endpoint()+"/revisions",json={
               "triage_id":self.triage_id,"idempotency_key":"revision-001234"})
        self.assertEqual(second.status_code,201)
        self.assertTrue(second.json()["deduplicated"])
        self.assertEqual(second.json()["id"],x["id"])
        with core.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM repo_agent_pr_revisions").fetchone()[0],1)

    def test_stale_head_rejects_owner_approval_without_put(self):
        x=self.propose()
        self.complete(x)
        self.http_calls.clear()
        self.sha="f"*40
        with self.mocked_http():
            r=self.owner_approve(x["id"])
        self.assertEqual(r.status_code,409,r.text)
        stored=self.client.get(self.endpoint()+"/revisions/"+x["id"]).json()
        self.assertEqual(stored["status"],"stale")
        self.assertFalse(any(m=="PUT" for m,_,_ in self.http_calls))

    def test_source_changed_rejects_and_does_not_put(self):
        x=self.propose()
        self.complete(x)
        self.blob="c"*40
        with self.mocked_http():
            result=self.owner_approve(x["id"])
        self.assertEqual(result.status_code,409,result.text)
        self.assertEqual(self.client.get(self.endpoint()+"/revisions/"+x["id"]).json()["status"],"stale")
        self.assertFalse(any(m=="PUT" for m,_,_ in self.http_calls))

    def test_uncertain_github_write_requires_reconciliation_no_retry(self):
        x=self.propose()
        self.complete(x)
        self.put_fail=True
        with self.mocked_http():
            result=self.owner_approve(x["id"])
        self.assertEqual(result.status_code,502)
        stored=self.client.get(self.endpoint()+"/revisions/"+x["id"]).json()
        self.assertEqual(stored["status"],"needs_reconciliation")
        count=sum(m=="PUT" for m,_,_ in self.http_calls)
        with self.mocked_http():
            result=self.owner_approve(x["id"])
        self.assertEqual(result.status_code,409)
        self.assertEqual(count,sum(m=="PUT" for m,_,_ in self.http_calls))

    def test_without_admin_or_write_token_rejects_before_state_change(self):
        x=self.propose()
        self.complete(x)
        no_admin=self.client.post(self.endpoint()+"/revisions/"+x["id"]+"/approve")
        self.assertEqual(no_admin.status_code,403)
        with patch.dict(os.environ,{"LHM_GITHUB_WRITE_TOKEN":""}):
            no_write=self.owner_approve(x["id"])
        self.assertEqual(no_write.status_code,424)
        self.assertEqual(self.client.get(self.endpoint()+"/revisions/"+x["id"]).json()["status"],"proposed")
        self.assertFalse(any(m=="PUT" for m,_,_ in self.http_calls))

    def test_invalid_llm_json_not_approvable(self):
        x=self.propose()
        self.complete(x,raw="Malformed JSON error")
        response=self.client.get(self.endpoint()+"/revisions/"+x["id"])
        self.assertEqual(response.status_code,200)
        self.assertFalse(response.json()["can_approve"])
        self.assertIsNone(response.json()["diff_preview"])
        with self.mocked_http():
            approve=self.owner_approve(x["id"])
        self.assertEqual(approve.status_code,409)
        self.assertFalse(any(m=="PUT" for m,_,_ in self.http_calls))

    def test_no_failed_ci_or_non_draft_pr_never_queues_worker(self):
        self.check_result="success"
        with self.mocked_http():
            no_error=self.client.post(self.endpoint()+"/revisions",json={
                "triage_id":self.triage_id,"idempotency_key":"revision-001234"})
        self.assertEqual(no_error.status_code,409)
        self.check_result="failure"
        self.pr_draft=False
        with self.mocked_http():
            no_draft=self.client.post(self.endpoint()+"/revisions",json={
                "triage_id":self.triage_id,"idempotency_key":"revision-001234"})
        self.assertEqual(no_draft.status_code,409)
        with core.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM repo_agent_pr_revisions").fetchone()[0],0)

    def test_foreign_agent_workspace_and_wrong_triage_blocked(self):
        bad=self.endpoint().replace("agent-cnlg","agent-autotax")
        self.assertEqual(self.client.post(bad+"/revisions",json={
            "triage_id":self.triage_id,"idempotency_key":"revision-001234"}).status_code,404)
        other=self.endpoint().replace("/demo/","/other/")
        self.assertEqual(self.client.post(other+"/revisions",json={
            "triage_id":self.triage_id,"idempotency_key":"revision-001234"}).status_code,404)
        self.assertEqual(self.client.post(self.endpoint()+"/revisions",json={
            "triage_id":core.new_id(),"idempotency_key":"revision-001234"}).status_code,404)
        self.assertEqual(self.http_calls,[])

    def test_reject_is_local_and_blocks_approval(self):
        x=self.propose()
        declined=self.client.post(self.endpoint()+"/revisions/"+x["id"]+"/reject")
        self.assertEqual(declined.status_code,200,declined.text)
        self.assertEqual(declined.json()["status"],"rejected")
        self.assertEqual(self.client.get(self.endpoint()+"/revisions/"+x["id"]).json()["can_approve"],False)
        with self.mocked_http():
            done=self.owner_approve(x["id"])
        self.assertEqual(done.status_code,409)
        self.assertFalse(any(m=="PUT" for m,_,_ in self.http_calls))

    def test_untrusted_pr_source_with_secret_never_sent_to_ai(self):
        self.source='OPENROUTER_API_KEY="secret"\n' + self.source
        with self.mocked_http():
            r=self.client.post(self.endpoint()+"/revisions",json={
                "triage_id":self.triage_id,"idempotency_key":"revision-001234"})
        self.assertEqual(r.status_code,422)
        with core.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM repo_agent_pr_revisions").fetchone()[0],0)

    def test_ui_revision_actions_and_javascript_syntax(self):
        html=self.client.get("/repo-agents/ui").text
        for item in ('id="prRevise"','id="prRevisionRead"',
                     'id="prRevisionApprove"','id="prRevisionResult"'):
            self.assertIn(item,html)
        node=shutil.which("node")
        if not node:self.skipTest("Node missing")
        js=re.search(r"<script>(.*?)</script>",html,re.S)
        self.assertIsNotNone(js)
        with tempfile.NamedTemporaryFile("w",suffix=".js",encoding="utf-8") as f:
            f.write(js.group(1));f.flush()
            run=subprocess.run([node,"--check",f.name],capture_output=True,text=True)
        self.assertEqual(run.returncode,0,run.stderr)


if __name__=="__main__":
    unittest.main()
