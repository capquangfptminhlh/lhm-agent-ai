"""Windows owner P4 blockers: fail-closed app launcher, Calculator UWP and dialogs.

Offline policy tests; a separate Windows CI step verifies real OS behavior.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

import computer_agent
import computer_executor
import windows_native as wn


class WindowsLaunchPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="lhm-native-launch-")
        self.root=Path(self.tmp.name)
        (self.root/"Documents"/"LHM-QA").mkdir(parents=True)
        self.env=patch.dict(os.environ,{
            "USERPROFILE":str(self.root),
            "SYSTEMROOT":str(self.root),
            "LHM_DESKTOP_ALLOWED_APPS":"notepad.exe,calc.exe,explorer.exe"
        })
        self.env.start()
        self.native=patch.object(wn,"_require_windows",return_value=None)
        self.native.start()

    def tearDown(self):
        self.native.stop()
        self.env.stop()
        self.tmp.cleanup()

    def test_no_shell_browser_or_arbitrary_binary_can_launch(self):
        self.assertEqual(wn.LAUNCHABLE,
            frozenset({"notepad.exe","explorer.exe","calc.exe"}))
        with patch("subprocess.Popen",side_effect=AssertionError("Must not spawn")):
            for exe in ("cmd.exe","powershell.exe","msedge.exe","python.exe",
                        "notepad.exe --args","../notepad.exe","C:/Windows/notepad.exe"):
                with self.subTest(exe=exe),self.assertRaises(HTTPException) as err:
                    wn._launch({"app":exe})
                self.assertEqual(err.exception.status_code,403)
            with self.assertRaises(HTTPException):
                wn._launch({"app":"notepad.exe","arbitrary":"field"})

    def test_allowed_launch_requires_owner_allowlist_and_exe_path(self):
        with patch.dict(os.environ,{"LHM_DESKTOP_ALLOWED_APPS":"notepad.exe"}), \
             patch("subprocess.Popen") as popen:
            with self.assertRaises(HTTPException) as err:
                wn._launch({"app":"calc.exe"})
            self.assertEqual(err.exception.status_code,403)
            popen.assert_not_called()
            receipt=wn._launch({"app":"notepad.exe"})
            self.assertEqual(receipt["launched"],"notepad.exe")
            self.assertIs(receipt["verified_visible"],False)
            args,kwargs=popen.call_args
            self.assertEqual(args[0],[str(self.root/"System32"/"notepad.exe")])
            self.assertIs(kwargs["shell"],False)

    def test_explorer_path_is_inside_documents(self):
        expected=os.path.realpath(self.root/"Documents"/"LHM-QA")
        self.assertEqual(wn._documents_folder("LHM-QA"),expected)
        self.assertEqual(wn._documents_folder(r"Documents\LHM-QA"),expected)
        for value in ("..","../..",str(self.root),str(self.root/"Documents"/".."/"AppData")):
            with self.subTest(path=value), self.assertRaises(HTTPException):
                wn._documents_folder(value)
        with self.assertRaises(HTTPException) as err:
            wn._documents_folder("not-found")
        self.assertEqual(err.exception.status_code,404)
        with patch("subprocess.Popen") as popen:
            wn._launch({"app":"explorer.exe","folder":"LHM-QA"})
            self.assertEqual(popen.call_args.args[0],
                [str(self.root/"explorer.exe"),expected])
        with patch("subprocess.Popen") as popen:
            with self.assertRaises(HTTPException):
                wn._launch({"app":"notepad.exe","folder":"LHM-QA"})
            popen.assert_not_called()

    def test_calculator_alias_is_exactly_one_app(self):
        self.assertTrue(wn._allowed_process("calculatorapp.exe",{"calc.exe"}))
        self.assertFalse(wn._allowed_process("calculatorapp.exe",{"notepad.exe"}))
        self.assertFalse(wn._allowed_process("applicationframehost.exe",{"calc.exe"}))
        self.assertFalse(wn._allowed_process("chrome.exe",{"calc.exe"}))

    def test_launch_registered_as_paused_and_requires_approval_in_all_modes(self):
        self.assertIn("windows_launch",computer_executor.WINDOWS_TOOLS)
        self.assertIn("windows_launch",computer_executor.MUTATIONS)
        computer_executor.ToolRequest(workspace_id="demo",role="research",
            tool="windows_launch",args={"app":"notepad.exe"})
        for mode in ("supervised","trusted"):
            self.assertTrue(computer_agent._approval_required(
                "windows_launch",{"mode":mode}))
        runner=TestClient(computer_executor.runner)
        with patch.dict(os.environ,{"LHM_COMPUTER_RUNNER_TOKEN":"test-token"}):
            try:
                computer_executor.PAUSED=True
                response=runner.post("/v1/tool",
                    headers={"Authorization":"Bearer test-token"},
                    json={"workspace_id":"demo","role":"research",
                          "tool":"windows_launch","args":{"app":"notepad.exe"}})
                self.assertEqual(response.status_code,423,response.text)
            finally:
                computer_executor.PAUSED=False


class WindowGeometryAndDialogTests(unittest.TestCase):
    def test_restore_rechecks_window_rect_not_old_minimized_coordinates(self):
        hwnd=1234
        before={"hwnd":hwnd,"x":-32000,"y":-32000,"width":500,"height":300}
        after={**before,"x":100,"y":150}
        class U32:
            def ShowWindow(self,*args):return True
            def SetForegroundWindow(self,*args):return True
            def GetForegroundWindow(self):return hwnd
        with patch.object(wn,"_window_info",side_effect=[before,after]) as lookup, \
             patch.object(wn,"_user32",return_value=U32()):
            result=wn._focus(hwnd)
        self.assertEqual((result["x"],result["y"]),(100,150))
        self.assertEqual(lookup.call_count,2)

    def test_utility_popup_hosts_do_not_hide_save_dialog(self):
        notepad=4444
        entries=[(300+i,"Microsoft.UI.Content.PopupWindowSiteBridge",notepad,"PopupHost")
                 for i in range(90)]
        entries += [(6000,"#32770",notepad,"Save as"),
                    (notepad,"Notepad",0,"Untitled - Notepad")]
        meta={h:(cls,owner,title) for h,cls,owner,title in entries}
        class U32:
            def GetClassNameW(self,hwnd,buf,size):
                buf.value=meta[hwnd.value][0]
            def GetWindow(self,hwnd,kind):
                assert kind==wn.GW_OWNER
                return meta[hwnd.value][1] or None
        def info(hwnd):
            return {"hwnd":hwnd,"process":"notepad.exe",
                    "title":meta[hwnd][2],"x":0,"y":0,
                    "width":800,"height":600}
        with patch.object(wn,"_window_info",side_effect=info):
            ordered=wn._order_windows(U32(),[h for h,*_ in entries])
        self.assertEqual([w["title"] for w in ordered],["Save as","Untitled - Notepad"])
        self.assertEqual((ordered[0]["owner_hwnd"],ordered[0]["dialog"]),(notepad,True))
        self.assertNotIn("dialog",ordered[1])


if __name__=="__main__":
    unittest.main()
