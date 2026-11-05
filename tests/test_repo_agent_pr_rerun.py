"""Run real ASGI+SQLite owner-approved CI Actions rerun; mock only GitHub HTTP."""
from __future__ import annotations

from datetime import datetime,timezone
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
import repo_agent_pr_rerun as rerun


class RepoAgentActionsRerunTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        original=core.DATABASE
        core.DATABASE=Path(temp.name)/"rerun.db"
        self.addCleanup(setattr,core,"DATABASE",original)
        core.setup()
        rerun.prepare_db()
        p=patch.dict(os.environ,{"LHM_ADMIN_TOKEN":"",
                "LHM_GITHUB_READ_TOKEN":"read-ci-only",
                "LHM_GITHUB_ACTIONS_TOKEN":"write-actions-only",
                "LHM_GITHUB_WRITE_TOKEN":""})
        p.start();self.addCleanup(p.stop)
        self.client=TestClient(app.app)
        self.repair=core.new_id()
        self.repo="capquangfptminhlh/cnlg"
        self.branch="agent-repair/agent-cnlg/123abc"
        self.sha="a"*40
        self.run_id=54321
        self.fail_ci=True
        self.run_conclusion="failure"
        self.run_name="CI Test Workflow"
        self.run_path=".github/workflows/ci-tests.yml"
        self.run_attempt=1
        self.actions_response=201
        self.calls=[]
        with core.db() as c:
            t=app.task_record(c,"demo","quality","Assess CI","A failure",
                              model="openrouter/free")
            c.execute("UPDATE tasks SET status='completed',result='Observed error' WHERE id=?",
                      (t["id"],))
            diag=core.new_id()
            c.execute("""INSERT INTO repo_agent_diagnoses
              (id,workspace_id,agent_id,repository,run_id,task_id,created_at)
              VALUES(?,?,?,?,?,?,?)""",(diag,"demo","agent-cnlg",self.repo,2,
                                          t["id"],core.now()))
            c.execute("""INSERT INTO repo_agent_repairs
              (id,workspace_id,agent_id,repository,diagnosis_id,path,
               base_head_sha,source_blob_sha,new_content,diff_preview,
               explanation,idempotency_key,status,branch,pr_number,
               created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (self.repair,"demo","agent-cnlg",self.repo,diag,"src/index.js",
               "b"*40,"c"*40,"code","-old\n+new","Owner reviewed",
               "owner-key-1234","pr_created",self.branch,79,core.now(),core.now()))

    def workflow(self):
        return {
            "id":self.run_id,"run_attempt":self.run_attempt,
            "head_sha":self.sha,"head_branch":self.branch,
            "name":self.run_name,"path":self.run_path,"event":"pull_request",
            "status":"completed","conclusion":self.run_conclusion,
            "created_at":datetime.now(timezone.utc).isoformat()
        }

    def handler(self,req):
        self.calls.append(req)
        self.assertEqual(req.url.host,"api.github.com")
        self.assertTrue(req.url.path.startswith("/repos/"+self.repo+"/"))
        self.assertEqual(req.headers.get("accept"),"application/vnd.github+json")
        if req.url.path.endswith("/pulls/79"):
            return httpx.Response(200,json={"number":79,"state":"open",
                "merged_at":None,"draft":True,
                "head":{"sha":self.sha,"ref":self.branch,
                        "repo":{"full_name":self.repo}},
                "base":{"ref":"main","repo":{"full_name":self.repo}}})
        if req.url.path.endswith("/commits/"+self.sha+"/check-runs"):
            return httpx.Response(200,json={
                "total_count":1,"check_runs":[
                    {"name":"Quality Gate","status":"completed",
                     "conclusion":"failure" if self.fail_ci else "success"}]})
        if req.url.path.endswith("/commits/"+self.sha+"/status"):
            return httpx.Response(200,json={"statuses":[]})
        if req.url.path.endswith("/actions/runs"):
            self.assertEqual(req.url.params.get("head_sha"),self.sha)
            return httpx.Response(200,json={"total_count":1,
                                            "workflow_runs":[self.workflow()]})
        if req.url.path.endswith("/actions/runs/"+str(self.run_id)):
            return httpx.Response(200,json=self.workflow())
        if req.url.path.endswith("/actions/runs/"+str(self.run_id)+"/rerun-failed-jobs"):
            self.assertEqual(req.method,"POST")
            self.assertEqual(req.headers.get("authorization"),"Bearer write-actions-only")
            self.assertEqual(req.content,b'{"enable_debug_logging":false}')
            return httpx.Response(self.actions_response)
        self.fail("Unallowed GitHub request: "+str(req.url))

    def scope(self):
        old=httpx.AsyncClient
        return patch.object(rerun.httpx,"AsyncClient",side_effect=lambda *a,**kw:
                            old(*a,transport=httpx.MockTransport(self.handler),**kw))

    def base(self):
        return "/workspaces/demo/repo-agents/agent-cnlg/repairs/"+self.repair

    def discover(self):
        with self.scope():
            return self.client.get(self.base()+"/rerunnable-runs")

    def submit(self):
        with patch.dict(os.environ,{"LHM_ADMIN_TOKEN":"test-admin"}):
            with self.scope():
                return self.client.post(self.base()+"/rerun-failed",
                  json={"run_id":self.run_id},
                  headers={"Authorization":"Bearer test-admin"})

    def test_real_owner_action_selects_exact_head_and_reruns_failed_jobs(self):
        found=self.discover()
        self.assertEqual(found.status_code,200,found.text)
        d=found.json()
        self.assertEqual(d["runs"][0]["run_id"],54321)
        self.assertFalse(d["runs"][0]["previous_request"])
        self.assertEqual(d["head_sha"],self.sha)
        self.assertTrue(all(r.method=="GET" for r in self.calls))
        self.calls.clear()
        result=self.submit()
        self.assertEqual(result.status_code,201,result.text)
        body=result.json()
        self.assertEqual(body["status"],"requested")
        self.assertEqual(body["run_attempt"],1)
        self.assertTrue(body["github_request_submitted"])
        self.assertTrue(body["rerun_is_not_test_success"])
        self.assertFalse(body["deployed_verified"])
        writes=[r for r in self.calls if r.method=="POST"]
        self.assertEqual(len(writes),1)
        self.assertIn("rerun-failed-jobs",writes[0].url.path)
        history=self.client.get(self.base()+"/rerun-history")
        self.assertEqual(history.status_code,200)
        self.assertEqual(history.json()["history"][0]["run_id"],54321)
        self.assertEqual(self.discover().json()["runs"][0]["previous_request"],True)

    def test_duplicate_owner_submission_cannot_repeat_github_rerun(self):
        self.assertEqual(self.submit().status_code,201)
        self.calls.clear()
        again=self.submit()
        self.assertEqual(again.status_code,201,again.text)
        self.assertTrue(again.json()["deduplicated"])
        self.assertFalse(any(x.method=="POST" for x in self.calls))
        with core.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM repo_agent_pr_reruns").fetchone()[0],1)

    def test_missing_actions_token_never_reuses_git_code_write_token(self):
        with patch.dict(os.environ,{"LHM_GITHUB_ACTIONS_TOKEN":"",
                                  "LHM_GITHUB_WRITE_TOKEN":"git-code-token"}):
            with patch.dict(os.environ,{"LHM_ADMIN_TOKEN":"test-admin"}):
                r=self.client.post(self.base()+"/rerun-failed",
                    json={"run_id":self.run_id},
                    headers={"Authorization":"Bearer test-admin"})
        self.assertEqual(r.status_code,424)
        self.assertFalse(self.calls)

    def test_admin_guard_blocks_before_workflow_read(self):
        r=self.client.post(self.base()+"/rerun-failed",json={"run_id":self.run_id})
        self.assertEqual(r.status_code,403)
        self.assertEqual(self.calls,[])

    def test_green_ci_disables_actions_rerun(self):
        self.fail_ci=False
        self.assertEqual(self.discover().status_code,409)
        self.assertEqual(self.submit().status_code,409)
        self.assertFalse(any(x.method=="POST" for x in self.calls))

    def test_unsafe_deploy_workflow_filtered_even_when_failed(self):
        self.run_name="CI plus Deploy Production"
        listing=self.discover()
        self.assertEqual(listing.status_code,200)
        self.assertEqual(listing.json()["runs"],[])
        self.assertEqual(self.submit().status_code,409)
        self.assertFalse(any(x.method=="POST" for x in self.calls))

    def test_branch_and_sha_mismatch_fail_before_side_effect(self):
        self.branch="agent-repair/agent-cnlg/updated"
        # Locally stored branch has not changed; GitHub returns different branch
        self.assertEqual(self.discover().status_code,409)
        self.assertFalse(any(x.method=="POST" for x in self.calls))

    def test_actions_conflict_is_needs_reconciliation_never_retry(self):
        self.actions_response=403
        self.assertEqual(self.submit().status_code,424)
        history=self.client.get(self.base()+"/rerun-history").json()["history"]
        self.assertEqual(history[0]["status"],"needs_reconciliation")
        self.calls.clear()
        again=self.submit()
        self.assertEqual(again.status_code,201)
        self.assertTrue(again.json()["deduplicated"])
        self.assertFalse(any(x.method=="POST" for x in self.calls))

    def test_other_workspace_or_agent_never_discovers_workflow(self):
        self.assertEqual(self.client.get(self.base().replace("/demo/","/other/")+
                                         "/rerunnable-runs").status_code,404)
        self.assertEqual(self.client.get(self.base().replace("agent-cnlg","agent-autotax")+
                                         "/rerunnable-runs").status_code,404)
        self.assertEqual(self.calls,[])

    def test_ui_actions_select_and_javascript_syntax(self):
        html=self.client.get("/repo-agents/ui").text
        for item in ('id="prRunDiscover"','id="prRunSelect"',
                     'id="prRunApprove"','id="prRunStatus"'):
            self.assertIn(item,html)
        node=shutil.which("node")
        if not node:self.skipTest("node unavailable")
        script=re.search(r"<script>(.*?)</script>",html,re.S)
        self.assertIsNotNone(script)
        with tempfile.NamedTemporaryFile("w",suffix=".js",encoding="utf-8") as f:
            f.write(script.group(1));f.flush()
            result=subprocess.run([node,"--check",f.name],
                                  capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)


if __name__=="__main__":
    unittest.main()
