"""Parity+ (P1/P2/Office3D): drag, Alt+Tab, desktop coords, grounding,
Trusted mode gates, vendored Three.js.

Offline only: no live LLM, no real Windows HWND, no public website.
Windows-native execution itself still requires acceptance on a logged-in
Windows 10/11 machine; these tests prove the gates and pure logic.
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
import robot_vision as vision
import windows_native as wn


class PureLogicTests(unittest.TestCase):
    def test_alt_tab_is_the_only_os_shortcut_added(self):
        self.assertEqual(wn._validate_hotkey("Alt+Tab"), "ALT+TAB")
        self.assertEqual(wn._validate_hotkey("alt+shift+tab"), "ALT+SHIFT+TAB")
        for bad in ("WIN+R", "CTRL+ALT+DELETE", "ALT+F4", "CTRL+SHIFT+ESC"):
            with self.assertRaises(HTTPException, msg=bad):
                wn._validate_hotkey(bad)

    def test_drag_points_must_stay_inside_window(self):
        window = {"x": 100, "y": 50, "width": 800, "height": 600}
        self.assertEqual(
            wn._drag_points(window, {"x": 10, "y": 10, "to_x": 200, "to_y": 120}),
            (110, 60, 300, 170))
        with self.assertRaises(HTTPException):
            wn._drag_points(window, {"x": 10, "y": 10, "to_x": 2000, "to_y": 120})
        with self.assertRaises(HTTPException):
            wn._drag_points(window, {"x": 10, "y": 10, "to_x": 10, "to_y": 10})

    def test_desktop_point_uses_virtual_screen_bounds(self):
        metrics = (-1920, 0, 3840, 1080)  # second monitor on the left
        self.assertEqual(wn._desktop_point({"x": -100, "y": 500}, metrics), (-100, 500))
        with self.assertRaises(HTTPException):
            wn._desktop_point({"x": 5000, "y": 10}, metrics)
        with self.assertRaises(HTTPException):
            wn._desktop_point({"x": "10", "y": 10}, metrics)

    def test_grounding_parse_is_strict(self):
        point = vision.parse_grounding('{"x": 500, "y": 250}', 1000, 500)
        self.assertEqual((point["x_px"], point["y_px"]), (500, 125))
        fenced = vision.parse_grounding('```json\n{"x": 0, "y": 1000, "label": "OK"}\n```', 800, 600)
        self.assertEqual((fenced["x_px"], fenced["y_px"], fenced["label"]), (0, 599, "OK"))
        for bad in ('{"x": -1, "y": -1}', '{"x": 1200, "y": 5}', 'nhấn nút ở giữa màn hình',
                    '{"x": "500", "y": 10}', '{}'):
            with self.assertRaises(ValueError, msg=bad):
                vision.parse_grounding(bad, 800, 600)

    def test_png_size_reads_ihdr(self):
        header = b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR" \
            + (1440).to_bytes(4, "big") + (900).to_bytes(4, "big")
        self.assertEqual(vision._png_size(header + b"\x00" * 16), (1440, 900))
        with self.assertRaises(HTTPException):
            vision._png_size(b"not a png at all, definitely")

    def test_locate_validation(self):
        tool, args = vision.validate_locate({"source": "browser", "target": "Nút Đăng nhập"})
        self.assertEqual(tool, "browser_screenshot")
        self.assertEqual(args, {})
        with self.assertRaises(HTTPException):
            vision.validate_locate({"source": "browser", "target": "ab"})
        with self.assertRaises(HTTPException):
            vision.validate_locate({"source": "browser", "target": "ok target", "extra": 1})


class ApprovalMatrixTests(unittest.TestCase):
    def test_supervised_unchanged(self):
        run = {"mode": "supervised"}
        self.assertTrue(agent._approval_required("windows_click", run))
        self.assertTrue(agent._approval_required("windows_drag", run))
        self.assertFalse(agent._approval_required("list_files", run))

    def test_trusted_requires_installation_optin(self):
        run = {"mode": "trusted"}
        with patch.dict(os.environ, {"LHM_COMPUTER_TRUSTED_ENABLED": "0"}):
            # Even a run recorded as trusted falls back to supervision when
            # the installation opt-in is off (fail closed).
            self.assertTrue(agent._approval_required("windows_click", run))
        with patch.dict(os.environ, {"LHM_COMPUTER_TRUSTED_ENABLED": "1"}):
            self.assertFalse(agent._approval_required("windows_click", run))
            self.assertFalse(agent._approval_required("browser_fill", run))
            self.assertFalse(agent._approval_required("write_file", run))
            # Never automatic in any mode:
            for tool in ("run_command", "sandbox_run", "vision_observe", "vision_locate",
                         "windows_desktop_click", "windows_desktop_drag",
                         "windows_desktop_scroll", "windows_desktop_screenshot"):
                self.assertTrue(agent._approval_required(tool, run), tool)


class TrustedModeApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="lhm-parity-tests-")
        self.original = core.DATABASE
        core.DATABASE = Path(self.tmp.name) / "db.sqlite3"
        core.setup()
        agent.prepare_db()
        self.env = patch.dict(os.environ, {
            "LHM_ADMIN_TOKEN": "",
            "LHM_COMPUTER_RUNNER_TOKEN": "test-only-runner-secret",
            "LHM_COMPUTER_URL": "http://computer:7781",
            "LHM_COMPUTER_AUTO_WRITE": "0",
            "LHM_COMPUTER_ALLOWED_HOSTS": "example.com",
            "LHM_COMPUTER_TRUSTED_ENABLED": "0",
        })
        self.env.start()
        self.client = TestClient(app.app)

    def tearDown(self):
        self.env.stop()
        core.DATABASE = self.original
        self.tmp.cleanup()

    def create(self, mode="supervised", max_steps=10, key="parity-case-001"):
        return self.client.post("/workspaces/demo/computer/runs", json={
            "role": "research", "objective": "Nghiên cứu dữ liệu công khai và ghi báo cáo",
            "max_steps": max_steps, "mode": mode, "idempotency_key": key})

    def test_trusted_rejected_without_optin(self):
        r = self.create(mode="trusted")
        self.assertEqual(r.status_code, 403, r.text)

    def test_supervised_step_cap_still_15(self):
        r = self.create(mode="supervised", max_steps=30)
        self.assertEqual(r.status_code, 422, r.text)

    def test_trusted_create_and_checkpoint_flow(self):
        with patch.dict(os.environ, {"LHM_COMPUTER_TRUSTED_ENABLED": "1"}):
            r = self.create(mode="trusted", max_steps=30)
            self.assertEqual(r.status_code, 201, r.text)
            run = r.json()
            self.assertEqual(run["mode"], "trusted")
            rid = run["id"]
            with core.db() as c:
                c.execute("UPDATE computer_runs SET step_count=25 WHERE id=?", (rid,))
            result = asyncio.run(agent.computer_tick(rid))
            self.assertEqual(result["status"], "awaiting_checkpoint")
            resumed = self.client.post(f"/computer/runs/{rid}/checkpoint/approve")
            self.assertEqual(resumed.status_code, 200, resumed.text)
            self.assertEqual(resumed.json()["status"], "queued")
            self.assertEqual(resumed.json()["checkpoint_passed"], 1)
            again = self.client.post(f"/computer/runs/{rid}/checkpoint/approve")
            self.assertEqual(again.status_code, 409)

    def test_trusted_auto_executes_routine_but_never_code(self):
        with patch.dict(os.environ, {"LHM_COMPUTER_TRUSTED_ENABLED": "1"}):
            run = self.create(mode="trusted", key="parity-case-auto").json()
            url = "/computer/runs/" + run["id"] + "/tick"
            with patch.object(agent, "_next_action", AsyncMock(return_value=("windows_click", {"hwnd": 7, "x": 5, "y": 5}))):
                with patch.object(agent, "runner_call", AsyncMock(return_value={"clicked": True})):
                    response = self.client.post(url)
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()["status"], "queued")
            run2 = self.create(mode="trusted", key="parity-case-code").json()
            url2 = "/computer/runs/" + run2["id"] + "/tick"
            with patch.object(agent, "_next_action", AsyncMock(return_value=("run_command", {"argv": ["python", "-c", "print(1)"]}))) :
                response = self.client.post(url2)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["status"], "awaiting_approval")


class PostVerifyTests(unittest.TestCase):
    """P3: verify-after-act inside computer_tick with a scripted runner."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="lhm-p3-tests-")
        self.original = core.DATABASE
        core.DATABASE = Path(self.tmp.name) / "db.sqlite3"
        core.setup()
        agent.prepare_db()
        self.env = patch.dict(os.environ, {
            "LHM_ADMIN_TOKEN": "",
            "LHM_COMPUTER_RUNNER_TOKEN": "test-only-runner-secret",
            "LHM_COMPUTER_URL": "http://computer:7781",
            "LHM_COMPUTER_AUTO_WRITE": "1",
            "LHM_COMPUTER_ALLOWED_HOSTS": "example.com",
        })
        self.env.start()
        self.client = TestClient(app.app)

    def tearDown(self):
        self.env.stop()
        core.DATABASE = self.original
        self.tmp.cleanup()

    def _run_and_tick(self, tool, args, runner_side_effect):
        r = self.client.post("/workspaces/demo/computer/runs", json={
            "role": "research", "objective": "Ghi file ket-qua.txt trong sandbox",
            "max_steps": 8, "idempotency_key": "p3-" + tool})
        self.assertEqual(r.status_code, 201, r.text)
        rid = r.json()["id"]

        async def fake_runner(run, called_tool, called_args):
            return runner_side_effect(called_tool, called_args)

        with patch.object(agent, "_next_action", AsyncMock(return_value=(tool, args))):
            with patch.object(agent, "runner_call", AsyncMock(side_effect=fake_runner)):
                response = self.client.post("/computer/runs/" + rid + "/tick")
        self.assertEqual(response.status_code, 200, response.text)
        with core.db() as c:
            step = c.execute("SELECT * FROM computer_steps WHERE run_id=? ORDER BY number",
                             (rid,)).fetchone()
        return dict(step)

    def test_write_file_verified_by_read_back(self):
        def side(tool, args):
            if tool == "write_file":
                return {"path": "ket-qua.txt", "bytes": 5}
            if tool == "read_file":
                return {"path": "ket-qua.txt", "content": "hello"}
            raise AssertionError(tool)

        step = self._run_and_tick("write_file",
                                  {"path": "ket-qua.txt", "content": "hello"}, side)
        self.assertEqual(step["status"], "completed")
        result = __import__("json").loads(step["result_json"])
        self.assertEqual(result["_verification"], {"method": "read_back", "ok": True})

    def test_write_file_mismatch_fails_the_step(self):
        def side(tool, args):
            if tool == "write_file":
                return {"path": "ket-qua.txt", "bytes": 5}
            if tool == "read_file":
                return {"path": "ket-qua.txt", "content": "other"}
            raise AssertionError(tool)

        step = self._run_and_tick("write_file",
                                  {"path": "ket-qua.txt", "content": "hello"}, side)
        self.assertEqual(step["status"], "failed")
        self.assertIn("verification", (step["error"] or "").lower())

    def test_windows_click_captures_uia_post_state(self):
        def side(tool, args):
            if tool == "windows_click":
                return {"clicked": True, "hwnd": 7}
            if tool == "windows_snapshot":
                return {"hwnd": 7, "title": "Untitled - Notepad",
                        "children": [{"n": 1}, {"n": 2}, {"n": 3}]}
            raise AssertionError(tool)

        async def fake_runner(run, called_tool, called_args):
            return side(called_tool, called_args)

        r = self.client.post("/workspaces/demo/computer/runs", json={
            "role": "research", "objective": "Bấm thử trong Notepad",
            "max_steps": 8, "idempotency_key": "p3-windows-click"})
        self.assertEqual(r.status_code, 201, r.text)
        rid = r.json()["id"]
        url = "/computer/runs/" + rid + "/tick"
        with patch.object(agent, "_next_action",
                          AsyncMock(return_value=("windows_click", {"hwnd": 7, "x": 5, "y": 5}))):
            with patch.object(agent, "runner_call", AsyncMock(side_effect=fake_runner)):
                first = self.client.post(url)
                self.assertEqual(first.json()["status"], "awaiting_approval")
                step_id = first.json()["step_id"]
                approved = self.client.post(
                    f"/computer/runs/{rid}/steps/{step_id}/approve")
                self.assertEqual(approved.status_code, 200, approved.text)
                second = self.client.post(url)
                self.assertEqual(second.status_code, 200, second.text)
        with core.db() as c:
            step = dict(c.execute("SELECT * FROM computer_steps WHERE id=?",
                                  (step_id,)).fetchone())
        self.assertEqual(step["status"], "completed")
        result = __import__("json").loads(step["result_json"])
        self.assertEqual(result["_verification"]["method"], "uia_snapshot")
        self.assertEqual(result["_verification"]["controls"], 3)
        self.assertIsNone(result["_verification"]["ok"])


class ExecutorRegistryTests(unittest.TestCase):
    def test_new_tools_registered_and_fail_closed_off_windows(self):
        for tool in ("windows_drag", "windows_desktop_click",
                     "windows_desktop_drag", "windows_desktop_scroll"):
            self.assertIn(tool, executor.WINDOWS_TOOLS)
            self.assertIn(tool, executor.ALLOWED_TOOLS)
            self.assertIn(tool, executor.MUTATIONS)
        client = TestClient(executor.runner)
        with patch.dict(os.environ, {"LHM_COMPUTER_RUNNER_TOKEN": "test-only-runner-secret",
                                     "LHM_DESKTOP_NATIVE": "0"}):
            r = client.post("/v1/tool", headers={"Authorization": "Bearer test-only-runner-secret"},
                            json={"workspace_id": "demo", "role": "research",
                                  "tool": "windows_drag",
                                  "args": {"hwnd": 1, "x": 1, "y": 1, "to_x": 9, "to_y": 9}})
            self.assertEqual(r.status_code, 503, r.text)


class Office3DVendorTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app.app)

    def test_three_js_is_served_locally(self):
        page = self.client.get("/office3d")
        self.assertEqual(page.status_code, 200)
        self.assertIn("/office3d/vendor/three.module.js", page.text)
        self.assertNotIn("cdn.jsdelivr.net/npm/three", page.text)
        lib = self.client.get("/office3d/vendor/three.module.js")
        self.assertEqual(lib.status_code, 200)
        self.assertGreater(len(lib.content), 100_000)
        # r186 splits the build: three.module.js re-exports three.core.js.
        # If the core file is not served, the browser module graph 404s and
        # the scene falls back (this exact bug failed browser-webgl CI once).
        self.assertIn(b"three.core.js", lib.content)
        core = self.client.get("/office3d/vendor/three.core.js")
        self.assertEqual(core.status_code, 200)
        self.assertGreater(len(core.content), 1_000_000)
        controls = self.client.get("/office3d/vendor/addons/controls/OrbitControls.js")
        self.assertEqual(controls.status_code, 200)
        self.assertIn("OrbitControls", controls.text)


if __name__ == "__main__":
    unittest.main()
