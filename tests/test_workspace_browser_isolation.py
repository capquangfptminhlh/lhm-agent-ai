"""Each workspace (hồ sơ) gets its own browsers; shared desktop belongs to one.

These tests must FAIL if workspace B can reuse workspace A's Chrome profile,
host list, Playwright profile directory, desktop windows or Android devices.
"""
import asyncio
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

import computer_agent
import computer_executor as executor
import opencli_windows_bridge as bridge

PROFILES = json.dumps({
    "shop-a": {"profile": "lhm-shop-a", "hosts": ["a.example.com"]},
    "shop-b": {"profile": "lhm-shop-b", "hosts": ["b.example.com"]},
})


class OpenCLIProfilePerWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "LHM_COMPUTER_ALLOWED_HOSTS": "a.example.com,b.example.com",
            "LHM_COMPUTER_CONTENT_ALLOWED_HOSTS": "a.example.com,b.example.com",
            "LHM_OPENCLI_PROFILES": PROFILES,
            "LHM_OPENCLI_PROFILE": "", "LHM_OPENCLI_ALLOWED_HOSTS": "",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_each_workspace_resolves_only_its_own_profile(self):
        self.assertEqual(bridge.workspace_profile("shop-a")["profile"], "lhm-shop-a")
        self.assertEqual(bridge.workspace_profile("shop-b")["profile"], "lhm-shop-b")
        with self.assertRaises(HTTPException) as err:
            bridge.workspace_profile("shop-c")
        self.assertEqual(err.exception.status_code, 409)
        self.assertFalse(bridge.configured_for("shop-c"))

    def test_hosts_of_another_workspace_are_never_allowed(self):
        self.assertTrue(bridge.allowed_url("https://a.example.com/x", "content", "shop-a"))
        self.assertFalse(bridge.allowed_url("https://a.example.com/x", "content", "shop-b"))
        self.assertFalse(bridge.allowed_url("https://b.example.com/x", "content", "shop-a"))
        self.assertFalse(bridge.allowed_url("https://a.example.com/x", "content", "shop-c"))
        with self.assertRaises(HTTPException):
            bridge.validate("content", "opencli_open", {"url": "https://a.example.com/x"}, "shop-b")

    def test_shared_or_malformed_profile_config_fails_closed_for_everyone(self):
        shared = json.dumps({"shop-a": {"profile": "lhm-work", "hosts": ["a.example.com"]},
                             "shop-b": {"profile": "LHM-WORK", "hosts": ["b.example.com"]}})
        for raw in (shared, "{not json", "[]",
                    json.dumps({"Shop A": {"profile": "x", "hosts": []}}),
                    json.dumps({"shop-a": {"profile": "bad:profile", "hosts": []}})):
            with self.subTest(raw=raw), patch.dict(os.environ, {"LHM_OPENCLI_PROFILES": raw}):
                with self.assertRaises(HTTPException) as err:
                    bridge.workspace_profiles()
                self.assertEqual(err.exception.status_code, 409)
                self.assertFalse(bridge.configured_for("shop-a"))
                self.assertFalse(bridge.allowed_url("https://a.example.com/x", "content", "shop-a"))

    def test_legacy_single_profile_belongs_only_to_its_workspace(self):
        with patch.dict(os.environ, {"LHM_OPENCLI_PROFILES": "", "LHM_OPENCLI_PROFILE": "lhm-work",
                                     "LHM_OPENCLI_ALLOWED_HOSTS": "a.example.com",
                                     "LHM_OPENCLI_WORKSPACE": ""}):
            self.assertTrue(bridge.configured_for("demo"))
            self.assertFalse(bridge.configured_for("shop-b"))
            self.assertTrue(bridge.allowed_url("https://a.example.com/x", "content", "demo"))
            self.assertFalse(bridge.allowed_url("https://a.example.com/x", "content", "shop-b"))
            with patch.dict(os.environ, {"LHM_OPENCLI_WORKSPACE": "shop-a"}):
                self.assertFalse(bridge.configured_for("demo"))
                self.assertTrue(bridge.configured_for("shop-a"))


class OpenCLIExecutionUsesWorkspaceProfileTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env = patch.dict(os.environ, {
            "LHM_COMPUTER_ALLOWED_HOSTS": "a.example.com,b.example.com",
            "LHM_COMPUTER_CONTENT_ALLOWED_HOSTS": "a.example.com,b.example.com",
            "LHM_OPENCLI_PROFILES": PROFILES, "LHM_OPENCLI_ENABLED": "1",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    async def test_chrome_profile_passed_to_opencli_is_the_callers_own(self):
        seen = []

        async def fake_invoke(node, entry, profile, session, action, *argv):
            seen.append((profile, session, action))
            host = "a.example.com" if profile == "lhm-shop-a" else "b.example.com"
            return {"data": {"url": f"https://{host}/x"}}

        with patch.object(bridge, "_toolchain", return_value=("node.exe", "main.js")), \
             patch.object(bridge, "_invoke", side_effect=fake_invoke):
            await bridge.execute("shop-a", "content", "opencli_open", {"url": "https://a.example.com/x"})
            await bridge.execute("shop-b", "content", "opencli_open", {"url": "https://b.example.com/x"})
            with self.assertRaises(HTTPException):
                await bridge.execute("shop-b", "content", "opencli_open", {"url": "https://a.example.com/x"})
            with self.assertRaises(HTTPException) as err:
                await bridge.execute("shop-c", "content", "opencli_open", {"url": "https://a.example.com/x"})
        self.assertEqual(err.exception.status_code, 403)
        self.assertEqual([p for p, _, _ in seen], ["lhm-shop-a", "lhm-shop-b"])
        self.assertNotEqual(seen[0][1], seen[1][1])


class DesktopBelongsToOneWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"LHM_COMPUTER_RUNNER_TOKEN": "isolation-test-token",
                                           "LHM_DESKTOP_WORKSPACE": "shop-a"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.client = TestClient(executor.runner)
        self.auth = {"Authorization": "Bearer isolation-test-token"}

    def call(self, ws, tool, args):
        return self.client.post("/v1/tool", headers=self.auth, json={
            "workspace_id": ws, "role": "research", "tool": tool, "args": args})

    def test_other_workspace_cannot_see_or_drive_any_desktop_window(self):
        native = patch.object(executor, "__native_tool", return_value={"windows": []})
        with native as called:
            for tool, args in (("windows_list", {}), ("windows_snapshot", {"hwnd": 101}),
                               ("windows_screenshot", {"hwnd": 101}), ("windows_desktop_screenshot", {}),
                               ("windows_click", {"hwnd": 101, "x": 5, "y": 5})):
                with self.subTest(tool=tool):
                    denied = self.call("shop-b", tool, args)
                    self.assertEqual(denied.status_code, 403, denied.text)
            called.assert_not_called()

    def test_other_workspace_cannot_touch_android_devices(self):
        with patch("android_mcp_bridge.execute", AsyncMock(return_value={})) as android:
            denied = self.call("shop-b", "android_mcp_devices", {})
        self.assertEqual(denied.status_code, 403, denied.text)
        android.assert_not_awaited()

    def test_owner_workspace_reaches_the_native_controller(self):
        with patch.object(executor, "__native_tool", return_value={"windows": []}) as called:
            allowed = self.call("shop-a", "windows_list", {})
        if os.name == "nt" and os.environ.get("LHM_DESKTOP_NATIVE") == "1":
            self.assertEqual(allowed.status_code, 200, allowed.text)
            called.assert_called_once()
        else:
            self.assertEqual(allowed.status_code, 503, allowed.text)

    def test_unset_owner_defaults_to_demo_only(self):
        with patch.dict(os.environ, {"LHM_DESKTOP_WORKSPACE": ""}):
            self.assertEqual(executor.desktop_workspace(), "demo")
            self.assertEqual(self.call("shop-a", "windows_list", {}).status_code, 403)

    def test_agent_prompt_offers_desktop_tools_only_to_owner(self):
        with patch.dict(os.environ, {"LHM_DESKTOP_NATIVE": "1", "LHM_OPENCLI_ENABLED": "1",
                                     "LHM_OPENCLI_PROFILES": json.dumps(
                                         {"shop-b": {"profile": "lhm-shop-b", "hosts": []}})}):
            owner = computer_agent._system_prompt("content", None, "shop-a")
            other = computer_agent._system_prompt("content", None, "shop-b")
            stranger = computer_agent._system_prompt("content", None, "shop-c")
        self.assertIn("- windows_list:", owner)
        self.assertNotIn("- windows_", other)
        self.assertNotIn("- android_mcp_", other)
        self.assertNotIn("- opencli_open:", owner)
        self.assertIn("- opencli_open:", other)
        self.assertNotIn("- opencli_open:", stranger)


class PlaywrightProfilePerWorkspaceAndAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_workspace_and_agent_gets_its_own_profile_directory(self):
        launched = []

        class Page:
            url = "about:blank"
            def set_default_timeout(self, _): pass
            async def title(self): return ""

        class Context:
            pages = [Page()]
            async def route(self, *_): pass

        class Chromium:
            async def launch_persistent_context(self, path, **_):
                launched.append(Path(path).resolve())
                return Context()

        class Playwright:
            chromium = Chromium()

        class Starter:
            async def start(self): return Playwright()

        fake = types.ModuleType("playwright.async_api")
        fake.async_playwright = lambda: Starter()
        with tempfile.TemporaryDirectory(prefix="lhm-browser-iso-") as tmp, \
             patch.dict(sys.modules, {"playwright": types.ModuleType("playwright"),
                                      "playwright.async_api": fake}), \
             patch.object(executor, "ROOT", Path(tmp).resolve()), \
             patch.object(executor, "_PLAYWRIGHT", None), \
             patch.dict(executor.BROWSER_SESSIONS, clear=True), \
             patch.dict(executor.BROWSER_LOCKS, clear=True):
            for ws, role in (("shop-a", "content"), ("shop-b", "content"), ("shop-a", "research")):
                with self.assertRaises(HTTPException):
                    # No allowlisted host: navigation is refused after the
                    # per-workspace context has been created.
                    await executor._browser(ws, role, "browser_open", {"url": "https://x.example.com"})
            root = Path(tmp).resolve()
        self.assertEqual(launched, [root / "shop-a" / "content" / "browser",
                                    root / "shop-b" / "content" / "browser",
                                    root / "shop-a" / "research" / "browser"])
        self.assertEqual(len(set(launched)), 3)


if __name__ == "__main__":
    unittest.main()
