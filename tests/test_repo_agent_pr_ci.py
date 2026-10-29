"""End-to-end ASGI/SQLite tests for actual GitHub PR check verification.

Only remote GitHub HTTP is mocked; every route, workspace/repair binding
and verdict are exercised through real FastAPI.
"""
from __future__ import annotations

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
import repo_agent_repair as repair
import repo_agent_pr_ci as verifier


class RepairCIReadTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        prev=core.DATABASE
        core.DATABASE=Path(self.tmp.name)/"ci.sqlite3"
        self.addCleanup(setattr,core,"DATABASE",prev)
        core.setup()
        repair.prepare_db()
        env=patch.dict(os.environ,{"LHM_ADMIN_TOKEN":"",
                                "LHM_GITHUB_READ_TOKEN":"ci-read-only",
                                "LHM_GITHUB_WRITE_TOKEN":""})
        env.start()
        self.addCleanup(env.stop)
        self.client=TestClient(app.app)
        self.repairid=core.new_id()
        self.pr=22
        self.sha="b"*40
        self.branch="agent-repair/agent-cnlg/abcdef"
        with core.db() as c:
            task=app.task_record(c,"demo","quality","Diagnose CI failures",
                                  "failed",model="openrouter/free")
            c.execute("UPDATE tasks SET status='completed',result='A CI report' WHERE id=?",
                      (task["id"],))
            diagid=core.new_id()
            c.execute("""INSERT INTO repo_agent_diagnoses
                 (id,workspace_id,agent_id,repository,run_id,task_id,created_at)
                 VALUES(?,?,?,?,?,?,?)""",(diagid,"demo","agent-cnlg",
                 "capquangfptminhlh/cnlg",32,task["id"],core.now()))
            c.execute("""INSERT INTO repo_agent_repairs
                (id,workspace_id,agent_id,repository,diagnosis_id,path,
                 base_head_sha,source_blob_sha,new_content,diff_preview,explanation,
                 idempotency_key,status,branch,pr_number,created_at,updated_at)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (self.repairid,"demo","agent-cnlg","capquangfptminhlh/cnlg",
                diagid,"src/app.py","a"*40,"c"*40,"print('x')","-old\n+new",
                "Fixing known CI issue","owner-bridge-1234","pr_created",
                self.branch,22,core.now(),core.now()))
        self.check_runs=[
            {"name":"Quality Gate","status":"completed","conclusion":"success"},
            {"name":"Windows Installer","status":"completed","conclusion":"success"}]
        self.statuses=[{"context":"security/scanner","state":"success"}]
        self.total=2
        self.open=True
        self.merged=False
        self.ref=self.branch
        self.remote_repo="capquangfptminhlh/cnlg"
        self.visits=[]

    def api_response(self,req):
        self.visits.append(req)
        self.assertEqual(req.method,"GET")
        self.assertEqual(req.url.host,"api.github.com")
        self.assertTrue(req.url.path.startswith("/repos/capquangfptminhlh/cnlg/"))
        self.assertEqual(req.headers.get("authorization"),"Bearer ci-read-only")
        if req.url.path.endswith("/pulls/22"):
            return httpx.Response(200,json={
                "number":22,"state":"open" if self.open else "closed",
                "merged_at":"2026-10-29T01:00:00Z" if self.merged else None,
                "draft":True,
                "head":{"sha":self.sha,"ref":self.ref,
                        "repo":{"full_name":self.remote_repo}},
                "base":{"ref":"main","repo":{"full_name":"capquangfptminhlh/cnlg"}}
            })
        if req.url.path.endswith("/commits/"+self.sha+"/check-runs"):
            return httpx.Response(200,json={"total_count":self.total,
                                            "check_runs":self.check_runs})
        if req.url.path.endswith("/commits/"+self.sha+"/status"):
            return httpx.Response(200,json={"state":"success","statuses":self.statuses})
        return httpx.Response(404)

    def run_ci(self):
        original=httpx.AsyncClient
        with patch.object(verifier.httpx,"AsyncClient",side_effect=lambda *a,**kw:
                          original(*a,transport=httpx.MockTransport(self.api_response),**kw)):
            return self.client.get(self.url())

    def url(self):
        return ("/workspaces/demo/repo-agents/agent-cnlg/repairs/"+
                self.repairid+"/ci")

    def test_green_is_observed_not_a_claim_of_required_checks_or_deploy(self):
        r=self.run_ci()
        self.assertEqual(r.status_code,200,r.text)
        d=r.json()
        self.assertEqual(d["verdict"],"observed_checks_green")
        self.assertEqual(d["pr_url"],
                         "https://github.com/capquangfptminhlh/cnlg/pull/22")
        self.assertTrue(d["checked_from_github"])
        self.assertTrue(d["check_runs_complete"])
        for prop in ("required_checks_verified","review_approval_verified",
                     "automatically_merged","deployed_verified"):
            self.assertFalse(d[prop])
        self.assertEqual(d["head_sha"],self.sha)
        self.assertEqual(len(self.visits),3)
        self.assertEqual(self.visits[1].url.params.get("per_page"),"100")

    def test_one_failed_check_cannot_be_hidden_by_green_check_or_status(self):
        self.check_runs[1]["conclusion"]="failure"
        d=self.run_ci().json()
        self.assertEqual(d["verdict"],"failed_observed")
        self.assertEqual(d["check_runs"][1]["conclusion"],"failure")

    def test_old_commit_status_failure_blocks_green_check_runs(self):
        self.statuses.append({"context":"security/manual","state":"error"})
        self.assertEqual(self.run_ci().json()["verdict"],"failed_observed")

    def test_in_progress_never_passes(self):
        self.check_runs[1]={"name":"Windows Installer","status":"in_progress",
                            "conclusion":None}
        self.assertEqual(self.run_ci().json()["verdict"],"checks_running")

    def test_no_checks_is_unknown_not_green(self):
        self.check_runs=[]
        self.statuses=[]
        self.total=0
        self.assertEqual(self.run_ci().json()["verdict"],"unverified")

    def test_paginated_checks_never_become_green(self):
        self.total=150
        d=self.run_ci().json()
        self.assertEqual(d["verdict"],"unverified")
        self.assertFalse(d["check_runs_complete"])

    def test_unknown_neutral_check_result_not_success(self):
        self.check_runs[1]["conclusion"]="neutral"
        self.assertEqual(self.run_ci().json()["verdict"],"unverified")

    def test_malformed_status_not_passed(self):
        self.statuses=[{"context":"checker","state":"unknown"}]
        self.assertEqual(self.run_ci().json()["verdict"],"unverified")

    def test_pr_closed_or_merged_is_not_open_for_agent(self):
        self.open=False
        self.assertEqual(self.run_ci().json()["verdict"],"pr_not_open")
        self.open=True
        self.merged=True
        self.assertEqual(self.run_ci().json()["verdict"],"pr_not_open")

    def test_mismatched_repo_or_ref_blocks_without_reading_checks(self):
        self.remote_repo="attacker/other"
        r=self.run_ci()
        self.assertEqual(r.status_code,409)
        self.assertEqual(len(self.visits),1)
        self.remote_repo="capquangfptminhlh/cnlg"
        self.ref="unexpected-branch"
        self.visits.clear()
        self.assertEqual(self.run_ci().status_code,409)
        self.assertEqual(len(self.visits),1)

    def test_cannot_read_others_workspace_agent_or_non_published_repairs(self):
        missing=self.url().replace("/demo/","/other/")
        self.assertEqual(self.client.get(missing).status_code,404)
        self.assertEqual(self.client.get(self.url().replace("agent-cnlg","agent-autotax")).status_code,404)
        with core.db() as c:
            c.execute("UPDATE repo_agent_repairs SET status='awaiting_approval' WHERE id=?",
                      (self.repairid,))
        self.assertEqual(self.client.get(self.url()).status_code,409)
        self.assertFalse(self.visits)

    def test_repo_agents_ui_has_action_and_valid_javascript(self):
        html=self.client.get("/repo-agents/ui").text
        self.assertIn('id="prCheck"',html)
        self.assertIn('id="prCIResult"',html)
        self.assertIn('el("prCheck").onclick=async()=>',html)
        node=shutil.which("node")
        if not node:
            self.skipTest("Node not installed")
        script=re.search(r"<script>(.*?)</script>",html,re.S)
        self.assertIsNotNone(script)
        with tempfile.NamedTemporaryFile(mode="w",suffix=".js",encoding="utf-8") as item:
            item.write(script.group(1));item.flush()
            result=subprocess.run([node,"--check",item.name],
                                  capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_network_403_reports_unavailable_and_not_success(self):
        original=httpx.AsyncClient
        with patch.object(verifier.httpx,"AsyncClient",side_effect=lambda *a,**kw:
                          original(*a,transport=httpx.MockTransport(
                              lambda req:httpx.Response(403)),**kw)):
            response=self.client.get(self.url())
        self.assertEqual(response.status_code,424)
        self.assertNotIn("token",response.text.lower())


if __name__=="__main__":
    unittest.main()
