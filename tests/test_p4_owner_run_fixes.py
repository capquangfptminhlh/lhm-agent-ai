"""Regressions from the first owner-machine P4 run (2026-10-28, installer of
main 2d54892): every failure seen on the real Windows PC gets a test here.

Offline only: no live LLM, no real HWND, no process is spawned.
"""
from __future__ import annotations
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

import app
import core
import computer_agent as agent
import computer_executor as executor
import openrouter_provider as provider
import windows_native as wn


def reply(content):
    return {"choices": [{"message": {"content": content}}], "usage": {}}


class ActionParsingTests(unittest.TestCase):
    """Runs died with 'AI không trả JSON hành động hợp lệ' after 1-3 steps."""

    def setUp(self):
        # Same as the installed Electron runtime, where windows_* tools exist.
        self.env = patch.dict(os.environ, {"LHM_DESKTOP_NATIVE": "1"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_json_wrapped_in_prose_think_or_fences_is_accepted(self):
        for body in (
            'Tôi sẽ liệt kê cửa sổ trước.\n{"tool":"windows_list","args":{}}',
            '<think>cần mở Notepad</think>{"tool":"windows_list","args":{}}',
            '```json\n{"tool":"windows_list","args":{}}\n```\nXong.',
            '{"tool":"windows_list","args":{},"reason":"observe first"}',
            '{"tool":"windows_list"}',
        ):
            self.assertEqual(agent._parse_action(body), ("windows_list", {}), body)

    def test_first_action_object_wins_and_validation_still_applies(self):
        body = 'Plan: {"step":1} then {"tool":"browser_snapshot","args":{}} {"tool":"run_command","args":{}}'
        self.assertEqual(agent._parse_action(body), ("browser_snapshot", {}))
        with self.assertRaises(HTTPException) as bad:
            agent._parse_action('Sure: {"tool":"format_disk","args":{}}')
        self.assertEqual(bad.exception.status_code, 422)

    def test_no_action_is_still_rejected(self):
        for body in ("Tôi không chắc.", "", None, '{"args":{}}', '{"tool":"windows_list","args":[]}'):
            with self.assertRaises(HTTPException, msg=repr(body)) as bad:
                agent._parse_action(body)
            self.assertEqual(bad.exception.status_code, 502)

    def test_one_bounded_reask_on_malformed_reply(self):
        run = {"role": "director", "objective": "Mở Notepad và gõ chữ", "model": "local/test",
               "workspace_id": "demo"}
        good = reply('{"tool":"windows_list","args":{}}')
        with patch.object(agent, "model_configured", return_value=True), \
                patch.object(app, "deepseek_request", AsyncMock(side_effect=[reply("Chờ chút..."), good])) as call:
            self.assertEqual(asyncio.run(agent._next_action(run, [])), ("windows_list", {}))
        self.assertEqual(call.await_count, 2)
        self.assertIn("ONLY a single JSON object", call.await_args_list[1].args[2])
        with patch.object(agent, "model_configured", return_value=True), \
                patch.object(app, "deepseek_request", AsyncMock(return_value=reply("vẫn không có JSON"))) as call:
            with self.assertRaises(HTTPException):
                asyncio.run(agent._next_action(run, []))
        self.assertEqual(call.await_count, 2, "never more than one re-ask")


class PrecheckTests(unittest.TestCase):
    """Task 7 died on a 1200px scroll the runner refuses before acting."""

    def test_runner_limits_are_checked_before_execution(self):
        self.assertIn("1-1100", agent._precheck_args("browser_scroll", {"direction": "down", "pixels": 1200}))
        self.assertIsNone(agent._precheck_args("browser_scroll", {"direction": "down", "pixels": 800}))
        self.assertIn("not allowed", agent._precheck_args("windows_hotkey", {"hwnd": 5, "keys": "WIN+R"}))
        self.assertIsNone(agent._precheck_args("windows_hotkey", {"hwnd": 5, "keys": "ctrl+s"}))
        self.assertIn("hwnd", agent._precheck_args("windows_click", {"x": 1, "y": 2}))
        self.assertIn("steps", agent._precheck_args("windows_scroll", {"hwnd": 5, "steps": 20}))
        self.assertIsNone(agent._precheck_args("windows_list", {}))
        self.assertIn("app must be", agent._precheck_args("windows_launch", {"app": "msedge.exe"}))
        self.assertIsNone(agent._precheck_args("windows_launch", {"app": "notepad.exe"}))


class TickWithInvalidArgsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="lhm-p4-fix-")
        self.original = core.DATABASE
        core.DATABASE = Path(self.tmp.name) / "db.sqlite3"
        core.setup()
        agent.prepare_db()
        self.env = patch.dict(os.environ, {"LHM_ADMIN_TOKEN": "", "LHM_COMPUTER_RUNNER_TOKEN": "t",
                                           "LHM_COMPUTER_URL": "http://computer:7781"})
        self.env.start()
        self.client = TestClient(app.app)

    def tearDown(self):
        self.env.stop()
        core.DATABASE = self.original
        self.tmp.cleanup()

    def test_invalid_args_fail_the_step_not_the_run(self):
        run = self.client.post("/workspaces/demo/computer/runs", json={
            "role": "research", "objective": "Cuộn trang dài tới mục Privacy", "max_steps": 5,
            "idempotency_key": "p4-owner-run-scroll"}).json()
        runner = AsyncMock(side_effect=AssertionError("runner must not be called"))
        with patch.object(agent, "_next_action",
                          AsyncMock(return_value=("browser_scroll", {"direction": "down", "pixels": 1200}))), \
                patch.object(agent, "runner_call", runner):
            out = asyncio.run(agent.computer_tick(run["id"]))
        self.assertEqual((out["status"], out["step_status"]), ("queued", "failed"))
        view = self.client.get(f"/computer/runs/{run['id']}").json()
        self.assertEqual(view["status"], "queued")
        self.assertEqual(view["step_count"], 1)
        self.assertEqual(view["steps"][0]["status"], "failed")
        self.assertIn("nothing executed", view["steps"][0]["error"])
        runner.assert_not_awaited()


class FreeModelContactFilterTests(unittest.TestCase):
    """Task 4 was refused at step 0 for the fake test@example.com."""

    def test_reserved_test_domains_are_not_personal_contacts(self):
        for text in ('email "test@example.com"', "a@b.example.org", "qa@shop.test", "x@foo.invalid"):
            self.assertFalse(provider.has_real_contact(text), text)

    def test_real_contacts_are_still_refused(self):
        for text in ("gửi cho minh@gmail.com", "x@example.com.vn", "liên hệ 0912 345 678",
                     "test@example.com và chu@company.vn"):
            self.assertTrue(provider.has_real_contact(text), text)


class NativeLaunchAndUwpTests(unittest.TestCase):
    """Agent could not open any app; Windows 11 Calculator was invisible."""

    def test_uwp_calculator_alias_is_narrow(self):
        self.assertTrue(wn._allowed_process("calculatorapp.exe", {"calc.exe"}))
        self.assertTrue(wn._allowed_process("notepad.exe", {"notepad.exe"}))
        self.assertFalse(wn._allowed_process("calculatorapp.exe", {"notepad.exe"}))
        self.assertFalse(wn._allowed_process("applicationframehost.exe", {"calc.exe"}))

    def test_launcher_never_starts_a_browser_or_shell(self):
        self.assertEqual(wn.LAUNCHABLE, {"notepad.exe", "explorer.exe", "calc.exe"})
        with patch.dict(os.environ, {"LHM_DESKTOP_ALLOWED_APPS": "msedge.exe,notepad.exe"}), \
                patch("subprocess.Popen", side_effect=AssertionError("must not spawn")):
            for app_name in ("msedge.exe", "cmd.exe", "powershell.exe", "explorer.exe"):
                with self.assertRaises(HTTPException, msg=app_name) as denied:
                    wn._launch({"app": app_name})
                self.assertEqual(denied.exception.status_code, 403)

    def test_explorer_folder_is_jailed_to_documents(self):
        with tempfile.TemporaryDirectory() as home:
            (Path(home) / "Documents" / "LHM-P4").mkdir(parents=True)
            with patch.dict(os.environ, {"USERPROFILE": home}):
                expected = os.path.realpath(Path(home) / "Documents" / "LHM-P4")
                self.assertEqual(wn._documents_folder("LHM-P4"), expected)
                self.assertEqual(wn._documents_folder("Documents\\LHM-P4"), expected)
                for escape in ("..", "../..", home, os.path.join(home, "Documents", "..", "AppData")):
                    with self.assertRaises(HTTPException, msg=escape) as bad:
                        wn._documents_folder(escape)
                    self.assertIn(bad.exception.status_code, (403, 404))
                with self.assertRaises(HTTPException) as missing:
                    wn._documents_folder("khong-ton-tai")
                self.assertEqual(missing.exception.status_code, 404)

    def test_launch_is_a_paused_mutation_and_supervised_needs_approval(self):
        self.assertIn("windows_launch", executor.WINDOWS_TOOLS)
        self.assertIn("windows_launch", executor.MUTATIONS)
        executor.ToolRequest(workspace_id="demo", role="research", tool="windows_launch",
                             args={"app": "notepad.exe"})
        self.assertTrue(agent._approval_required("windows_launch", {"mode": "supervised"}))


if __name__ == "__main__":
    unittest.main()
