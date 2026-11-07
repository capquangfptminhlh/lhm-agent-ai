"""P3: an uncertain UI action must never trigger another automatic click.

Pure offline tests: no Windows device, real browser, LLM key or network needed.
Owner Windows P4 acceptance remains a separate requirement.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import app
import core
import computer_agent as agent


class ComputerUiReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="lhm-p3-ui-state-")
        self.original_db = core.DATABASE
        core.DATABASE = Path(self.tmp.name) / "db.sqlite3"
        core.setup()
        agent.prepare_db()
        self.env = patch.dict(os.environ, {
            "LHM_ADMIN_TOKEN": "",
            "LHM_COMPUTER_RUNNER_TOKEN": "test-only-secret",
            "LHM_COMPUTER_URL": "http://computer:7781",
            "LHM_COMPUTER_ALLOWED_HOSTS": "example.com",
        })
        self.env.start()
        self.client = TestClient(app.app)

    def tearDown(self):
        self.env.stop()
        core.DATABASE = self.original_db
        self.tmp.cleanup()

    def _approved_fill(self, suffix):
        response = self.client.post("/workspaces/demo/computer/runs", json={
            "role": "research", "objective": "Điền thử vào biểu mẫu an toàn",
            "max_steps": 8, "idempotency_key": "ui-state-" + suffix,
        })
        self.assertEqual(response.status_code, 201, response.text)
        rid = response.json()["id"]
        with patch.object(agent, "_next_action", AsyncMock(return_value=(
                "browser_fill", {"selector": "#name", "text": "example"}))):
            pending = self.client.post("/computer/runs/" + rid + "/tick")
        self.assertEqual(pending.status_code, 200, pending.text)
        self.assertEqual(pending.json()["status"], "awaiting_approval")
        sid = pending.json()["step_id"]
        approve = self.client.post("/computer/runs/" + rid + "/steps/" + sid + "/approve")
        self.assertEqual(approve.status_code, 200, approve.text)
        return rid

    def test_postflight_disappeared_stops_even_if_action_receipt_says_ok(self):
        rid = self._approved_fill("lost-postflight-001")
        read_before = {"url": "https://example.com/form", "title": "Test form"}
        with patch.object(agent, "runner_call", AsyncMock(side_effect=[
                read_before, {"filled": True}, RuntimeError("page vanished")])) as runner:
            executed = self.client.post("/computer/runs/" + rid + "/tick")
        self.assertEqual(executed.status_code, 200, executed.text)
        self.assertEqual(executed.json()["status"], "awaiting_reconciliation")
        self.assertEqual(executed.json()["step_status"], "needs_review")
        self.assertEqual(runner.await_count, 3)
        saved = self.client.get("/computer/runs/" + rid).json()
        self.assertEqual(saved["status"], "awaiting_reconciliation")
        result = saved["steps"][-1]["result"]
        self.assertTrue(result["filled"])
        self.assertTrue(result["_verification"]["requires_owner_reconciliation"])
        self.assertIsNone(result["_verification"]["post"])
        self.assertEqual(self.client.post("/computer/runs/" + rid + "/tick").status_code, 409)

    def test_closed_browser_before_action_blocks_without_mutation(self):
        rid = self._approved_fill("lost-preflight-003")
        with patch.object(agent, "runner_call", AsyncMock(
                side_effect=RuntimeError("browser closed"))) as runner:
            result = self.client.post("/computer/runs/" + rid + "/tick")
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["status"], "needs_review")
        self.assertEqual(result.json()["step_status"], "failed")
        self.assertIs(result.json()["action_executed"], False)
        self.assertEqual(runner.await_count, 1, "Never call browser_fill after failed snapshot")
        self.assertEqual(runner.await_args.args[1], "browser_snapshot")
        saved = self.client.get("/computer/runs/" + rid).json()
        self.assertEqual(saved["steps"][-1]["status"], "failed")
        self.assertIs(saved["steps"][-1]["result"]["_verification"]["action_executed"], False)
        self.assertEqual(self.client.post("/computer/runs/" + rid + "/tick").status_code, 409)

    def test_missing_url_preflight_blocks_browser_fill_before_action(self):
        rid = self._approved_fill("bad-browser-preflight-004")
        with patch.object(agent, "runner_call", AsyncMock(
                return_value={"title": "Opening..."})) as runner:
            result = self.client.post("/computer/runs/" + rid + "/tick")
        self.assertEqual(result.json()["status"], "needs_review")
        self.assertEqual(runner.await_count, 1)
        self.assertEqual(runner.await_args.args[1], "browser_snapshot")

    def test_closed_windows_app_blocks_approved_semantic_click(self):
        from fastapi import HTTPException
        response = self.client.post("/workspaces/demo/computer/runs", json={
            "role": "research", "objective": "Nhấn nút trong Notepad nếu còn mở",
            "max_steps": 8, "idempotency_key": "ui-state-closed-windows-005",
        })
        self.assertEqual(response.status_code, 201, response.text)
        rid = response.json()["id"]
        args = {"hwnd": 123, "automation_id": "save", "control_type": "Button", "label": "Lưu"}
        with patch.dict(os.environ, {"LHM_DESKTOP_NATIVE": "1"}):
            with patch.object(agent, "_next_action", AsyncMock(
                    return_value=("windows_click_element", args))):
                pending = self.client.post("/computer/runs/" + rid + "/tick")
            self.assertEqual(pending.json()["status"], "awaiting_approval")
            approval = self.client.post(
                "/computer/runs/" + rid + "/steps/" + pending.json()["step_id"] + "/approve")
            self.assertEqual(approval.status_code, 200, approval.text)
            with patch.object(agent, "runner_call", AsyncMock(
                    side_effect=HTTPException(404, "Window is not visible"))) as runner:
                halted = self.client.post("/computer/runs/" + rid + "/tick")
        self.assertEqual(halted.json()["status"], "needs_review")
        self.assertIs(halted.json()["action_executed"], False)
        self.assertEqual(runner.await_count, 1)
        self.assertEqual(runner.await_args.args[1], "windows_snapshot")
        self.assertEqual(self.client.post("/computer/runs/" + rid + "/tick").status_code, 409)

    def test_desktop_absolute_tools_keep_separate_consent_gate(self):
        # They lack a selected HWND, so this patch must not change their
        # existing explicit full-screen consent + manual approval policy.
        self.assertNotIn("windows_desktop_click", agent.UI_PREFLIGHT_TOOLS)
        self.assertNotIn("windows_desktop_drag", agent.UI_PREFLIGHT_TOOLS)


    def test_valid_pre_and_post_preserve_supervised_workflow(self):
        rid = self._approved_fill("valid-postflight-002")
        state = {"url": "https://example.com/form", "title": "Test form"}
        with patch.object(agent, "runner_call", AsyncMock(side_effect=[
                state, {"filled": True}, state])):
            executed = self.client.post("/computer/runs/" + rid + "/tick")
        self.assertEqual(executed.status_code, 200, executed.text)
        self.assertEqual(executed.json()["status"], "queued")
        saved = self.client.get("/computer/runs/" + rid).json()
        self.assertEqual(saved["steps"][-1]["status"], "completed")
        verification = saved["steps"][-1]["result"]["_verification"]
        self.assertIsNone(verification["ok"])
        self.assertFalse(verification["observed_change"])

    def test_changed_windows_process_identity_requires_owner(self):
        before = {"hwnd": 111, "pid": 123, "process": "notepad.exe",
                  "title": "Untitled", "controls": 1, "uia_fingerprint": "a"}
        after = {**before, "pid": 456}
        with patch.object(agent, "_capture_ui_state", AsyncMock(return_value=after)):
            verification = asyncio.run(agent._post_verify(
                {"id": "offline"}, "windows_click_element", {"hwnd": 111},
                {"clicked": True}, before))
        self.assertIsNone(verification["ok"])
        self.assertTrue(verification["requires_owner_reconciliation"])
        self.assertNotEqual(verification["pre"]["pid"], verification["post"]["pid"])

    def test_missing_preflight_is_not_accepted_as_success(self):
        with patch.object(agent, "_capture_ui_state", AsyncMock(return_value={
                "url": "https://example.com/form", "title": "ok"})):
            verification = asyncio.run(agent._post_verify(
                {}, "browser_fill", {"selector": "#name"}, {"filled": True}, None))
        self.assertTrue(verification["requires_owner_reconciliation"])
        self.assertIsNone(verification["ok"])


    def test_windows_uia_backend_failure_is_not_a_valid_snapshot(self):
        reply = {"window": {"hwnd": 111, "pid": 123, "process": "notepad.exe"},
                 "controls": [], "note": "UI Automation controls unavailable"}
        with patch.object(agent, "runner_call", AsyncMock(return_value=reply)):
            captured = asyncio.run(agent._capture_ui_state(
                {}, "windows_click_element", {"hwnd": 111}))
        self.assertIsNone(captured)

    def test_browser_snapshot_without_url_is_not_valid_evidence(self):
        with patch.object(agent, "runner_call", AsyncMock(return_value={"title": "Loading"})):
            captured = asyncio.run(agent._capture_ui_state(
                {}, "browser_fill", {"selector": "#name"}))
        self.assertIsNone(captured)


if __name__ == "__main__":
    unittest.main()
