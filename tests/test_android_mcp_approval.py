"""End-to-end Computer Agent approval and reconciliation for Android tap.

No live ADB device; verifies the API database state machine and prevents
execution before explicit approval, in Trusted mode and after rejection.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
import app
import core
import computer_agent as agent
import android_mcp_bridge as bridge


RUN_TOKEN="0123456789abcdef0123456789abcdef"
SERIAL="emulator-5554"
ARGS={"serial":SERIAL,"observation_id":RUN_TOKEN,"index":1}


class AndroidAgentApprovalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="lhm-android-approved-")
        self.old_db=core.DATABASE
        core.DATABASE=Path(self.tmp.name)/"db.sqlite3"
        core.setup()
        agent.prepare_db()
        self.env=patch.dict(os.environ,{
            "LHM_ADMIN_TOKEN":"",
            "LHM_DESKTOP_NATIVE":"1",
            "LHM_ANDROID_MCP_TAP_ENABLED":"1",
            "LHM_COMPUTER_RUNNER_TOKEN":"test-runner",
            "LHM_COMPUTER_TRUSTED_ENABLED":"1",
        })
        self.env.start()
        self.ready=patch.object(bridge,"configured",return_value=True)
        self.serials=patch.object(bridge,"allowed_serials",return_value={SERIAL})
        self.ready.start()
        self.serials.start()
        self.client=TestClient(app.app)

    def tearDown(self):
        self.serials.stop()
        self.ready.stop()
        self.env.stop()
        core.DATABASE=self.old_db
        self.tmp.cleanup()

    def create(self,key="approved-android-action-01"):
        response=self.client.post("/workspaces/demo/computer/runs",json={
            "role":"research",
            "objective":"Quan sát thử Android ảo và đề xuất chạm nút an toàn",
            "idempotency_key":key,
            "mode":"supervised",
            "max_steps":5
        })
        self.assertEqual(response.status_code,201,response.text)
        return response.json()["id"]

    def tick(self,rid):
        return self.client.post("/computer/runs/"+rid+"/tick")

    def test_approval_required_and_then_owner_reconciliation(self):
        rid=self.create()
        with patch.object(agent,"_next_action",AsyncMock(return_value=(
            "android_mcp_tap",ARGS))),patch.object(agent,"runner_call",
            AsyncMock()) as tool:
            waiting=self.tick(rid)
        self.assertEqual(waiting.status_code,200,waiting.text)
        self.assertEqual(waiting.json()["status"],"awaiting_approval")
        tool.assert_not_awaited()
        view=self.client.get("/computer/runs/"+rid).json()
        step=view["steps"][-1]
        self.assertEqual(step["tool"],"android_mcp_tap")
        self.assertEqual(step["status"],"awaiting_approval")
        receipt={
            "state":"tap_sent","serial":SERIAL,"index":1,"ui_after":"changed",
            "confirmed_by_cli":True,"requires_owner_reconciliation":True,
            "external_effect_verified":False
        }
        with patch.object(agent,"runner_call",AsyncMock(return_value=receipt)) as tool:
            approved=self.client.post("/computer/runs/"+rid+"/steps/"+step["id"]+"/approve")
            self.assertEqual(approved.status_code,200,approved.text)
            with patch.object(agent,"_next_action",side_effect=AssertionError("Approved step must not ask AI")):
                done=self.tick(rid)
        self.assertEqual(done.status_code,200,done.text)
        self.assertEqual(done.json()["status"],"awaiting_reconciliation")
        tool.assert_awaited_once()
        self.assertEqual(tool.await_args.args[1],"android_mcp_tap")
        self.assertEqual(tool.await_args.args[2],ARGS)
        self.assertEqual(self.tick(rid).status_code,409)
        paused=self.client.get("/computer/runs/"+rid).json()
        self.assertEqual(paused["steps"][-1]["status"],"needs_review")
        self.assertFalse(paused["steps"][-1]["result"]["external_effect_verified"])
        ack=self.client.post("/computer/runs/"+rid+"/reconciliation/acknowledge")
        self.assertEqual(ack.status_code,200,ack.text)
        self.assertEqual(ack.json()["status"],"queued")
        self.assertEqual(self.client.post("/computer/runs/"+rid+"/reconciliation/acknowledge").status_code,409)
        self.assertEqual(self.client.get("/computer/runs/"+rid).json()["steps"][-1]["status"],"reviewed_unverified")

    def test_reject_does_not_execute(self):
        rid=self.create("approved-android-action-02")
        with patch.object(agent,"_next_action",AsyncMock(return_value=(
            "android_mcp_tap",ARGS))),patch.object(agent,"runner_call",AsyncMock()) as tool:
            waiting=self.tick(rid)
        self.assertEqual(waiting.json()["status"],"awaiting_approval")
        tool.assert_not_awaited()
        sid=waiting.json()["step_id"]
        with patch.object(agent,"runner_call",AsyncMock()) as tool:
            denied=self.client.post("/computer/runs/"+rid+"/steps/"+sid+"/reject")
            self.assertEqual(denied.status_code,200,denied.text)
            tool.assert_not_awaited()
        self.assertEqual(self.client.post("/computer/runs/"+rid+"/steps/"+sid+"/approve").status_code,409)

    def test_revocation_before_approved_execution_fails_closed(self):
        rid=self.create("approved-android-action-03")
        with patch.object(agent,"_next_action",AsyncMock(return_value=(
            "android_mcp_tap",ARGS))):
            waiting=self.tick(rid).json()
        self.assertEqual(waiting["status"],"awaiting_approval")
        self.assertEqual(self.client.post("/computer/runs/"+rid+"/steps/"+waiting["step_id"]+"/approve").status_code,200)
        os.environ["LHM_ANDROID_MCP_TAP_ENABLED"]="0"
        with patch.object(agent,"runner_call",AsyncMock()) as runner:
            outcome=self.tick(rid)
        self.assertEqual(outcome.json()["status"],"needs_review")
        runner.assert_not_awaited()


if __name__=="__main__":
    unittest.main()
