"""Browser Find UIA fill is approved, local, and refuses wrong/ambiguous Edits.

Offline fake accessibility tree only. The real headed Edge run is the
Windows CI gate; this test never types into an actual application.
"""
from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

import computer_agent as agent
import computer_executor as executor
import windows_native as native


class FakeEdit:
    def __init__(self,aid,x,y):
        self.element_info=types.SimpleNamespace(
            control_type="Edit",automation_id=aid,
            rectangle=types.SimpleNamespace(
                left=x-80,right=x+80,top=y-9,bottom=y+9))
        self.filled=[]

    def set_edit_text(self,value):
        self.filled.append(value)


class FindUiAGuards(unittest.TestCase):
    def setUp(self):
        self.address=FakeEdit("view_1017",420,70)
        self.find=FakeEdit("view_1045",700,110)
        self.children=[types.SimpleNamespace(element_info=types.SimpleNamespace(
                           control_type="Text",automation_id="noise"))
                       for _ in range(80)]
        self.children[5]=self.address
        self.children.append(self.find)
        def desktop(*,backend):
            assert backend=="uia"
            return types.SimpleNamespace(window=lambda *,handle:
                types.SimpleNamespace(descendants=lambda:self.children))
        self.fake=patch.dict(sys.modules,{
            "pywinauto":types.SimpleNamespace(Desktop=desktop)})
        self.fake.start()
        self.windows=patch.object(native,"_require_windows",return_value=None)
        self.windows.start()
        self.win={"hwnd":88,"pid":200,"process":"msedge.exe",
                  "x":0,"y":0,"width":1070,"height":720}
        self.foreground=patch.object(native,"_foreground_or_refocus",
                                     return_value=self.win)
        self.foreground.start()
        self.check=patch.object(native,"_foreground",return_value=self.win)
        self.check.start()

    def tearDown(self):
        self.check.stop()
        self.foreground.stop()
        self.windows.stop()
        self.fake.stop()

    def _fill(self,aid="view_1045",text="Privacy"):
        return native.execute("windows_fill_find",{
            "hwnd":88,"automation_id":aid,"text":text})

    def test_only_lower_browser_find_edit_can_be_filled(self):
        receipt=self._fill()
        self.assertEqual(receipt["target"],"browser_find")
        self.assertIs(receipt["search_result_verified"],False)
        self.assertEqual(receipt["characters"],7)
        self.assertNotIn("Privacy",str(receipt))
        self.assertEqual(self.find.filled,["Privacy"])
        self.assertEqual(self.address.filled,[])

    def test_address_bar_id_is_denied_without_any_fill(self):
        with self.assertRaises(HTTPException) as err:
            self._fill("view_1017")
        self.assertEqual(err.exception.status_code,409)
        self.assertEqual(self.find.filled,[])
        self.assertEqual(self.address.filled,[])

    def test_missing_or_ambiguous_browser_find_denies(self):
        self.children.append(FakeEdit("view_1100",750,143))
        with self.assertRaises(HTTPException) as err:
            self._fill()
        self.assertEqual(err.exception.status_code,409)
        self.assertEqual(self.find.filled,[])

    def test_wrong_process_fails_before_visiting_uia_tree(self):
        self.win["process"]="notepad.exe"
        with self.assertRaises(HTTPException) as err:
            self._fill()
        self.assertEqual(err.exception.status_code,403)
        self.assertEqual(self.find.filled,[])

    def test_invalid_target_text_and_extra_fields_are_rejected(self):
        for aid,text in (("bad","Privacy"),("view_1017;rm","Privacy"),
                         ("view_1045","Xin chào"),("view_1045","a/b")):
            with self.subTest(aid=aid,text=text),self.assertRaises(HTTPException) as err:
                self._fill(aid,text)
            self.assertEqual(err.exception.status_code,422)
        with self.assertRaises(HTTPException) as err:
            native.execute("windows_fill_find",{
                "hwnd":88,"automation_id":"view_1045",
                "text":"Privacy","selector":"#submit"})
        self.assertEqual(err.exception.status_code,422)
        self.assertEqual(self.find.filled,[])

    def test_approval_and_emergency_pause_stay_mandatory(self):
        self.assertIn("windows_fill_find",executor.MUTATIONS)
        self.assertIn("windows_fill_find",executor.WINDOWS_TOOLS)
        executor.ToolRequest(workspace_id="demo",role="research",
            tool="windows_fill_find",args={"hwnd":88,
            "automation_id":"view_1045","text":"Privacy"})
        self.assertTrue(agent._approval_required("windows_fill_find",
            {"mode":"trusted"}))
        self.assertTrue(agent._approval_required("windows_fill_find",
            {"mode":"supervised"}))
        self.assertIsNone(agent._precheck_args("windows_fill_find",{
            "hwnd":88,"automation_id":"view_1045","text":"Privacy"}))
        from fastapi.testclient import TestClient
        client=TestClient(executor.runner)
        with patch.dict(executor.os.environ,{"LHM_COMPUTER_RUNNER_TOKEN":"test-token"}):
            try:
                executor.PAUSED=True
                result=client.post("/v1/tool",
                    headers={"Authorization":"Bearer test-token"},
                    json={"workspace_id":"demo","role":"research",
                          "tool":"windows_fill_find",
                          "args":{"hwnd":88,"automation_id":"view_1045",
                                  "text":"Privacy"}})
                self.assertEqual(result.status_code,423,result.text)
            finally:
                executor.PAUSED=False


if __name__=="__main__":
    unittest.main()
