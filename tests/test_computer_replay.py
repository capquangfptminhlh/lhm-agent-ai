"""P3/G6: operator can inspect an immutable redacted receipt without replaying clicks.

Offline unit/integration tests. This is an evidence ledger, NOT video playback or
a claim that the owner Windows P4 run was independently verified.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import app
import computer_agent as agent
import core


class ComputerEvidenceReplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="lhm-p3-evidence-")
        self.previous_db=core.DATABASE
        core.DATABASE=Path(self.tmp.name)/"db.sqlite3"
        core.setup()
        agent.prepare_db()
        self.env=patch.dict(os.environ,{
            "LHM_ADMIN_TOKEN":"",
            "LHM_COMPUTER_RUNNER_TOKEN":"test-only-secret",
            "LHM_COMPUTER_URL":"http://computer:7781",
            "LHM_COMPUTER_AUTO_WRITE":"0",
            "LHM_COMPUTER_ALLOWED_HOSTS":"example.com"
        })
        self.env.start()
        self.client=TestClient(app.app)

    def tearDown(self):
        self.env.stop()
        core.DATABASE=self.previous_db
        self.tmp.cleanup()

    def _run(self,key):
        created=self.client.post("/workspaces/demo/computer/runs",json={
            "role":"research",
            "objective":"Kiểm tra lịch sử Agent trên môi trường thử nghiệm",
            "max_steps":8,"idempotency_key":key
        })
        self.assertEqual(created.status_code,201,created.text)
        return created.json()["id"]

    def _replay(self,rid,ws="demo"):
        return self.client.get(f"/workspaces/{ws}/computer/runs/{rid}/replay")

    def test_approved_file_receipt_survives_completion_without_secrets(self):
        rid=self._run("p3-replay-approved-001")
        secret="SENSITIVE-CLIENT-DATA-123456"
        with patch.object(agent,"_next_action",AsyncMock(return_value=(
                "write_file",{"path":"private/sensitive-report.txt","content":secret}))):
            pending=self.client.post(f"/computer/runs/{rid}/tick").json()
        self.assertEqual(pending["status"],"awaiting_approval",pending)
        sid=pending["step_id"]
        approved=self.client.post(f"/computer/runs/{rid}/steps/{sid}/approve")
        self.assertEqual(approved.status_code,200,approved.text)
        with patch.object(agent,"runner_call",AsyncMock(side_effect=[
                {"written":True,"path":"private/sensitive-report.txt"},
                {"content":secret}
        ])) as runner:
            out=self.client.post(f"/computer/runs/{rid}/tick")
            self.assertEqual(out.json()["status"],"queued",out.text)
            self.assertEqual(runner.await_count,2)

        with patch.object(agent,"runner_call",AsyncMock(
                side_effect=AssertionError("GET replay must never reach Runner"))) as runner:
            before=self._replay(rid).json()
            after=self._replay(rid).json()
            runner.assert_not_awaited()
        self.assertEqual(before,after)
        self.assertEqual(before["format"],"computer-redacted-replay-v1")
        self.assertTrue(before["read_only"])
        self.assertIs(before["actions_reexecuted"],False)
        self.assertIs(before["outcome_independently_verified"],False)
        self.assertEqual(len(before["steps"]),1)
        item=before["steps"][0]
        self.assertEqual(item["decision"],"approved")
        self.assertIsInstance(item["decided_at"],int)
        self.assertEqual(item["status"],"completed")
        self.assertEqual(item["verification"],
                         {"level":"deterministic_pass","method":"read_back"})
        types=[e["type"] for e in before["events"]]
        self.assertIn("computer.step_approved",types)
        self.assertIn("computer.tool_completed",types)
        self.assertEqual([e["id"] for e in before["events"]],
                         sorted(e["id"] for e in before["events"]))
        raw=json.dumps(before,ensure_ascii=False)
        self.assertNotIn(secret,raw)
        self.assertNotIn("sensitive-report",raw)
        self.assertNotIn("private/",raw)
        self.assertNotIn("args",raw)
        self.assertNotIn("summary",raw)
        self.assertNotIn("result_json",raw)

    def test_rejection_receipt_is_recorded_without_executing(self):
        rid=self._run("p3-replay-rejected-002")
        with patch.object(agent,"_next_action",AsyncMock(return_value=(
                "browser_click",{"selector":"#publish"}))):
            pending=self.client.post(f"/computer/runs/{rid}/tick").json()
        self.assertEqual(pending["status"],"awaiting_approval")
        sid=pending["step_id"]
        with patch.object(agent,"runner_call",AsyncMock(
                side_effect=AssertionError("Rejected actions must not execute"))) as runner:
            result=self.client.post(
                f"/computer/runs/{rid}/steps/{sid}/reject")
            runner.assert_not_awaited()
        self.assertEqual(result.status_code,200,result.text)
        step=self._replay(rid).json()["steps"][0]
        self.assertEqual(step["status"],"rejected")
        self.assertEqual(step["decision"],"rejected")
        self.assertEqual(step["verification"]["level"],"not_verified")

    def test_cross_workspace_and_missing_session_return_404(self):
        rid=self._run("p3-replay-scope-003")
        self.assertEqual(self._replay(rid,"another-workspace").status_code,404)
        self.assertEqual(self._replay("not-a-real-run").status_code,404)

    def test_token_auth_controls_replay(self):
        rid=self._run("p3-replay-auth-004")
        with patch.dict(os.environ,{"LHM_ADMIN_TOKEN":"test-admin-token"}):
            unauthorized=self._replay(rid)
            self.assertEqual(unauthorized.status_code,401)
            allowed=self.client.get(f"/workspaces/demo/computer/runs/{rid}/replay",
                headers={"Authorization":"Bearer test-admin-token"})
            self.assertEqual(allowed.status_code,200,allowed.text)

    def test_legacy_state_pair_keeps_only_hostname_and_fingerprint(self):
        rid=self._run("p3-replay-legacy-005")
        secret="TOP-SECRET-QUERY"
        evidence={"_verification":{
            "method":"state_pair","ok":None,"observed_change":True,
            "pre":{"url":"https://example.com/private?api_key="+secret,
                   "title":"User's private page"},
            "post":{"url":"https://example.com/confirmed?token="+secret,
                    "title":"Invoice for private client"},
        },"content":"Do not include this content "+secret,
          "png_base64":"unsafe-image-secret"}
        with core.db() as c:
            c.execute("""INSERT INTO computer_steps
              (id,run_id,number,tool,args_json,status,result_json,created_at,updated_at)
              VALUES (?,?,?,?,?,?,?,?,?)""",
              ("legacy-evidence-step",rid,1,"browser_click",
               json.dumps({"selector":"#send","text":secret}),
               "completed",json.dumps(evidence),core.now(),core.now()))
            core.audit(c,"demo","computer.tool_completed",
                       "sensitive event summary "+secret,rid)
        data=self._replay(rid).json()
        step=data["steps"][0]
        self.assertEqual(step["decision"],"not_recorded")
        self.assertEqual(step["verification"]["level"],"not_verified")
        self.assertIs(step["verification"]["observed_change"],True)
        self.assertEqual(step["verification"]["pre"],{"https_host":"example.com"})
        self.assertEqual(step["verification"]["post"],{"https_host":"example.com"})
        output=json.dumps(data,ensure_ascii=False)
        self.assertNotIn(secret,output)
        self.assertNotIn("private",output)
        self.assertNotIn("png_base64",output)
        self.assertNotIn("Invoice",output)

    def test_failed_preflight_is_not_reported_as_executed(self):
        rid=self._run("p3-replay-preflight-006")
        evidence={"_verification":{"method":"preflight","ok":False,
                                   "action_executed":False}}
        with core.db() as c:
            c.execute("""INSERT INTO computer_steps
              (id,run_id,number,tool,args_json,status,result_json,created_at,updated_at)
              VALUES (?,?,?,?,?,?,?,?,?)""",
              ("preflight-failed-step",rid,1,"windows_click",
               json.dumps({"hwnd":42,"x":5,"y":8}),"failed",
               json.dumps(evidence),core.now(),core.now()))
        step=self._replay(rid).json()["steps"][0]
        self.assertEqual(step["verification"],{"level":"failed","method":"preflight",
                                                "action_executed":False})
        self.assertEqual(step["decision"],"not_recorded")

    def test_broken_legacy_result_does_not_break_replay(self):
        rid=self._run("p3-replay-malformed-007")
        with core.db() as c:
            c.execute("""INSERT INTO computer_steps
              (id,run_id,number,tool,args_json,status,result_json,created_at,updated_at)
              VALUES (?,?,?,?,?,?,?,?,?)""",
              ("malformed-result",rid,1,"windows_click",
               '{"x":1}',"needs_review",'{invalid-json',
               core.now(),core.now()))
        body=self._replay(rid).json()
        self.assertEqual(body["steps"][0]["verification"]["level"],"unknown")


if __name__=="__main__":
    unittest.main()
