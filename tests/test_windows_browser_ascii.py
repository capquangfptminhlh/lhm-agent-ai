"""Physical ASCII input is narrow, browser-only and always supervised.

Pure offline Win32 mocks; headed Edge integration is in Windows CI.
"""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

import computer_agent as agent
import windows_native as native


class PhysicalBrowserTextTests(unittest.TestCase):
    def test_ascii_validator_rejects_controls_commands_and_unicode(self):
        for text in ("Privacy","hello 2026","SEARCH"):
            self.assertEqual(native._validate_physical_ascii(text),text)
        for text in ("",None,7,"Xin chào","a\nrun","a\tb","a/b",
                     "a.b","a:b","x"*121,"a@b","CTRL+F","a\\b"):
            with self.subTest(text=repr(text)),self.assertRaises(HTTPException) as ex:
                native._validate_physical_ascii(text)
            self.assertEqual(ex.exception.status_code,422)

    def test_virtual_keys_are_balanced_and_recheck_target_foreground(self):
        class U32:
            def __init__(self): self.keys=[]
            def keybd_event(self,vk,scan,flags,extra):self.keys.append((vk,flags))
            def GetKeyState(self,vk):return 0
        user=U32()
        with patch.object(native,"_user32",return_value=user), \
             patch.object(native,"_foreground",return_value={"process":"msedge.exe"}) as fg, \
             patch.object(native.time,"sleep",return_value=None):
            native._type_browser_ascii_keys("Ab 2",12345)
        self.assertEqual(fg.call_count,4)
        self.assertEqual(user.keys,[
            (0x10,0),(0x41,0),(0x41,2),(0x10,2),
            (0x42,0),(0x42,2),
            (0x20,0),(0x20,2),
            (0x32,0),(0x32,2),
        ])

    def test_non_browser_window_denied_without_any_keypress(self):
        with patch.object(native,"_require_windows",return_value=None), \
             patch.object(native,"_foreground_or_refocus",
                          return_value={"process":"notepad.exe"}), \
             patch.object(native,"_type_browser_ascii_keys",
                          side_effect=AssertionError("No input allowed")) as send:
            with self.assertRaises(HTTPException) as err:
                native.execute("windows_type",{"hwnd":99,"text":"Privacy",
                                            "input_method":"physical_ascii"})
        self.assertEqual(err.exception.status_code,403)
        send.assert_not_called()

    def test_allowed_browser_uses_ascii_method_without_echoing_value(self):
        with patch.object(native,"_require_windows",return_value=None), \
             patch.object(native,"_foreground_or_refocus",
                          return_value={"process":"msedge.exe"}), \
             patch.object(native,"_type_browser_ascii_keys") as send:
            receipt=native.execute("windows_type",{
                "hwnd":99,"text":"Privacy","input_method":"physical_ascii"})
        send.assert_called_once_with("Privacy",99)
        self.assertEqual(receipt["input_method"],"physical_ascii")
        self.assertEqual(receipt["characters"],7)
        self.assertNotIn("Privacy",str(receipt))

    def test_default_unicode_mode_for_notepad_is_unchanged(self):
        with patch.object(native,"_require_windows",return_value=None), \
             patch.object(native,"_foreground_or_refocus",
                          return_value={"process":"notepad.exe"}), \
             patch.object(native,"_type_unicode") as send:
            receipt=native.execute("windows_type",{"hwnd":5,"text":"Xin chào"})
        send.assert_called_once_with("Xin chào")
        self.assertEqual(receipt["input_method"],"unicode")
        self.assertNotIn("Xin chào",str(receipt))

    def test_bad_input_method_and_unsafe_chars_fail_precheck(self):
        self.assertIn("unicode",agent._precheck_args(
            "windows_type",{"hwnd":22,"text":"a","input_method":"shell"}))
        self.assertIn("ASCII",agent._precheck_args(
            "windows_type",{"hwnd":22,"text":"a/b","input_method":"physical_ascii"}))
        self.assertIsNone(agent._precheck_args(
            "windows_type",{"hwnd":22,"text":"Privacy",
                            "input_method":"physical_ascii"}))

    def test_trusted_mode_must_still_approve_typing_into_edge(self):
        run={"mode":"trusted"}
        args={"hwnd":42,"text":"Privacy","input_method":"physical_ascii"}
        snap={"window":{"hwnd":42,"pid":100,"process":"msedge.exe"},
              "controls":[]}
        with patch.dict(agent.os.environ,{"LHM_COMPUTER_TRUSTED_ENABLED":"1"}), \
             patch.object(agent,"runner_call",AsyncMock(return_value=snap)) as runner:
            self.assertTrue(asyncio.run(
                agent._approval_for_action("windows_type",args,run)))
            runner.assert_awaited_once()


if __name__=="__main__":
    unittest.main()
