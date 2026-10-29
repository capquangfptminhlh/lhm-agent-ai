"""Tests proving Windows controller never silently enables host-wide control.

Real HWND/cursor automation must be accepted on a logged-in Windows 10/11
machine. GitHub CI runs fail-closed tests, not fake successful GUI actions.
"""
from __future__ import annotations
import os
import unittest
from unittest.mock import patch
from fastapi import HTTPException
from fastapi.testclient import TestClient

import computer_executor as exe
import computer_agent as ca
import windows_native as wn


class WindowsNativeTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(exe.runner)
        self.auth={"Authorization":"Bearer test-windows-runner-secret"}
        self.env=patch.dict(os.environ,{
            "LHM_COMPUTER_RUNNER_TOKEN":"test-windows-runner-secret",
            "LHM_DESKTOP_NATIVE":"0",
            "LHM_DESKTOP_ALLOWED_APPS":"",
            "LHM_COMPUTER_HOME":"/tmp/lhm-test"
        })
        self.env.start()
        exe.PAUSED=False

    def tearDown(self):
        exe.PAUSED=False
        self.env.stop()

    def _post(self,tool,args,auth=True):
        return self.client.post("/v1/tool",headers=self.auth if auth else {},
             json={"workspace_id":"demo","role":"research",
                   "tool":tool,"args":args})

    def test_snapshot_exposes_bounded_uia_targets_without_secret_text(self):
        """Semantic controls give the Agent stable IDs and safe local click points."""
        import sys
        from types import ModuleType, SimpleNamespace

        window={"hwnd":123,"pid":42,"process":"notepad.exe",
                "title":"Notepad","x":100,"y":200,"width":400,"height":300}

        def control(kind,label,automation_id,rect,password=False):
            info=SimpleNamespace(control_type=kind,automation_id=automation_id,
                                 rectangle=SimpleNamespace(left=rect[0],top=rect[1],
                                                           right=rect[2],bottom=rect[3]),
                                 is_password=password)
            return SimpleNamespace(element_info=info,window_text=lambda:label)

        controls=[
            control("Button","Lưu","save-button",(120,230,200,270)),
            control("Edit","SECRET-DO-NOT-ECHO","input-password",(120,280,300,305)),
            control("Button","Offscreen","other",(40,40,80,80)),
            control("Text","SENSITIVE-PASSWORD","password",(200,310,280,350),True)
        ]
        module=ModuleType("pywinauto")
        module.Desktop=lambda backend:SimpleNamespace(
            window=lambda handle:SimpleNamespace(descendants=lambda:controls))
        with patch.object(wn,"_require_windows"),patch.object(
                wn,"_window_info",return_value=window),patch.dict(sys.modules,{"pywinauto":module}):
            result=wn.execute("windows_snapshot",{"hwnd":123})
        items=result["controls"]
        self.assertEqual(len(items),4)
        self.assertEqual(items[0]["automation_id"],"save-button")
        self.assertEqual(items[0]["center"],{"x":60,"y":50})
        self.assertEqual(items[0]["label"],"Lưu")
        self.assertNotIn("center",items[2])
        self.assertEqual(items[1]["label"],"")
        self.assertEqual(items[3]["label"],"")
        self.assertNotIn("SECRET-DO-NOT-ECHO",str(result))
        self.assertNotIn("SENSITIVE-PASSWORD",str(result))
        self.assertNotIn("value",items[0])

    def test_windows_mutation_unavailable_in_docker(self):
        response=self._post("windows_list",{})
        self.assertEqual(response.status_code,503,response.text)
        self.assertEqual(self._post("windows_click",{"hwnd":123,"x":10,"y":20}).status_code,503)
        self.assertEqual(self._post("windows_type",{"hwnd":123,"text":"Xin chào"}).status_code,503)

    def test_windows_executable_allowlist_empty_and_exact(self):
        self.assertEqual(wn._allowlist(),set())
        with patch.dict(os.environ,{
            "LHM_DESKTOP_ALLOWED_APPS":"notepad.exe, fake.exe, powershell,calc.exe,*.exe,cmd.exe,notepad.exe"
        }):
            self.assertEqual(wn._allowlist(),{"notepad.exe","fake.exe","calc.exe"})
        with patch.dict(os.environ,{"LHM_DESKTOP_ALLOWED_APPS":" " }):
            self.assertEqual(wn._allowlist(),set())

    def test_emergency_pause_blocks_mutations_in_runner(self):
        self.assertEqual(self.client.post("/v1/pause",json={"paused":True}).status_code,401)
        paused=self.client.post("/v1/pause",headers=self.auth,json={"paused":True})
        self.assertEqual(paused.status_code,200)
        self.assertTrue(paused.json()["paused"])
        self.assertTrue(self.client.get("/health",headers=self.auth).json()["paused"])
        for tool,args in [
            ("write_file",{"path":"test.txt","content":"should-not-write"}),
            ("edit_file",{"path":"test.txt","old":"a","new":"b"}),
            ("run_command",{"argv":["python","-V"]}),
            ("browser_click",{"selector":"#buy"}),
            ("windows_activate",{"hwnd":123}),
            ("windows_click",{"hwnd":123,"x":10,"y":20}),
            ("windows_type",{"hwnd":123,"text":"hello"}),
            ("windows_scroll",{"hwnd":123,"steps":1}),
            ("windows_right_click",{"hwnd":123,"x":10,"y":20}),
            ("windows_double_click",{"hwnd":123,"x":10,"y":20}),
            ("windows_hotkey",{"hwnd":123,"keys":"CTRL+S"}),
            ("windows_desktop_screenshot",{})
        ]:
            self.assertEqual(self._post(tool,args).status_code,423,tool)
        self.assertEqual(self.client.post("/v1/pause",headers=self.auth,
                      json={"paused":False}).status_code,200)
        self.assertFalse(self.client.get("/health",headers=self.auth).json()["paused"])

    def test_safe_shortcuts_never_allow_os_launcher(self):
        # ALT+TAB / ALT+SHIFT+TAB were deliberately added for multi-app work
        # (Parity+ P1, acceptance task 5). Every other OS launcher stays out.
        for key in ("CTRL+S", "CTRL+SHIFT+S", "ENTER", "ESC", "LEFT", "BACKSPACE",
                    "ALT+TAB", "ALT+SHIFT+TAB"):
            self.assertEqual(wn._validate_hotkey(key.lower()), key)
        for key in ("WIN+R", "CTRL+ALT+DELETE", "CTRL+SHIFT+ESC",
                    "ALT+F4", "CTRL+P", "", "S", "WIN+E"):
            with self.subTest(key=key):
                with self.assertRaises(HTTPException) as err:
                    wn._validate_hotkey(key)
                self.assertEqual(err.exception.status_code, 422)

    def test_full_desktop_capture_disabled_and_step_approval_required(self):
        with patch.object(wn,"_require_windows"):
            with patch.dict(os.environ,{"LHM_DESKTOP_FULL_SCREEN_ENABLED":"0"}):
                with self.assertRaises(HTTPException) as denied:
                    wn.execute("windows_desktop_screenshot",{})
                self.assertEqual(denied.exception.status_code,403)
        with self.assertRaises(HTTPException) as not_native:
            wn.execute("windows_desktop_screenshot",{})
        self.assertEqual(not_native.exception.status_code,503)
        self.assertTrue(ca._approval_required("windows_desktop_screenshot"))

    def test_new_mouse_hotkey_tools_are_native_only(self):
        for tool,args in [
            ("windows_right_click", {"hwnd":123,"x":20,"y":20}),
            ("windows_double_click", {"hwnd":123,"x":20,"y":20}),
            ("windows_hotkey", {"hwnd":123,"keys":"CTRL+S"}),
        ]:
            response=self._post(tool,args)
            self.assertEqual(response.status_code,503,(tool,response.text))
            self.assertTrue(ca._approval_required(tool))

    def test_native_process_key_env_is_not_exposed_by_runtime_code(self):
        # Desktop runner injects only selected Windows/system env vars,
        # not OPENROUTER_API_KEY or DEEPSEEK_API_KEY.
        from pathlib import Path
        p=Path(__file__).resolve().parents[1]/"desktop"/"main.cjs"
        code=p.read_text(encoding="utf-8")
        self.assertIn("function nativeEnv(runtimeEnv)",code)
        begin=code.index("function nativeEnv(runtimeEnv)")
        end=code.index("function runtimeCommand(",begin)
        segment=code[begin:end]
        self.assertNotIn('OPENROUTER_API_KEY',segment)
        self.assertNotIn('DEEPSEEK_API_KEY',segment)
        self.assertIn("LHM_COMPUTER_RUNNER_TOKEN",segment)

    def test_controller_exposes_no_shell_through_windows_action(self):
        self.assertFalse(hasattr(wn,"run_shell"))
        with self.assertRaises(HTTPException) as ctx:
            wn.execute("windows_click",{"hwnd":12,"x":24,"y":24})
        self.assertEqual(ctx.exception.status_code,503)

    def test_api_requires_bearer_even_for_readonly_native(self):
        self.assertEqual(self._post("windows_list",{},auth=False).status_code,401)
        self.assertEqual(self.client.get("/health").status_code,401)

    def test_llm_desktop_actions_are_always_approval_gated(self):
        for tool in ("windows_activate","windows_click","windows_type","windows_scroll",
                     "windows_right_click","windows_double_click","windows_hotkey",
                     "windows_desktop_screenshot"):
            self.assertTrue(ca._approval_required(tool))
        self.assertFalse(ca._approval_required("windows_list"))
        with patch.dict(os.environ,{"LHM_DESKTOP_NATIVE":"0"}):
            with self.assertRaises(HTTPException):
                ca._parse_action('{"tool":"windows_click","args":{"hwnd":1,"x":5,"y":5}}')
        with patch.dict(os.environ,{"LHM_DESKTOP_NATIVE":"1"}):
            tool,args=ca._parse_action('{"tool":"windows_activate","args":{"hwnd":1}}')
            self.assertEqual(tool,"windows_activate")

if __name__=="__main__":
    unittest.main()
