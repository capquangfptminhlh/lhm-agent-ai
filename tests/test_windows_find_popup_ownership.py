"""Foreground exception applies ONLY to an OS-owned same-PID Edge Find popup.

Never broadens normal click, typing or desktop permissions. No real UI.
"""
import unittest
from unittest.mock import patch
from fastapi import HTTPException
import windows_native as win


class FakeUser32:
    def __init__(self,fg=909,owner=88,root=88):
        self.fg=fg;self.owner=owner;self.root=root
    def GetForegroundWindow(self):return self.fg
    def GetWindow(self,hwnd,index):
        assert index==win.GW_OWNER
        return self.owner
    def GetAncestor(self,hwnd,index):
        assert index==3
        return self.root


class FindPopupOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.main={"hwnd":88,"pid":501,"process":"msedge.exe",
                   "x":0,"y":0,"width":800,"height":600}
        self.popup={"hwnd":909,"pid":501,"process":"msedge.exe",
                    "x":200,"y":45,"width":450,"height":80}
        self.ui=FakeUser32()

    def _check(self):
        with patch.object(win,"_window_info",
                          side_effect=lambda hwnd:self.main if hwnd==88 else self.popup), \
             patch.object(win,"_user32",return_value=self.ui):
            return win._foreground_owned_browser_find_popup(88)

    def test_exact_owner_and_same_process_allows_only_find_popup(self):
        self.assertIs(self._check(),self.main)

    def test_main_foreground_still_allowed(self):
        self.ui.fg=88
        self.assertIs(self._check(),self.main)

    def test_same_executable_but_other_profile_window_is_rejected(self):
        self.ui.owner=777
        with self.assertRaises(HTTPException) as err:
            self._check()
        self.assertEqual(err.exception.status_code,409)

    def test_same_executable_but_different_pid_is_rejected(self):
        self.popup["pid"]=911
        with self.assertRaises(HTTPException) as err:
            self._check()
        self.assertEqual(err.exception.status_code,409)

    def test_missing_rootowner_rejected(self):
        self.ui.root=777
        with self.assertRaises(HTTPException) as err:
            self._check()
        self.assertEqual(err.exception.status_code,409)

    def test_other_process_rejected_even_if_hwnd_owner_matches(self):
        self.popup["process"]="chrome.exe"
        with self.assertRaises(HTTPException) as err:
            self._check()
        self.assertEqual(err.exception.status_code,409)

    def test_non_browser_app_never_gets_popup_exception(self):
        self.main["process"]="notepad.exe"
        with self.assertRaises(HTTPException) as err:
            self._check()
        self.assertEqual(err.exception.status_code,403)

    def test_find_hotkeys_are_only_enter_escape(self):
        u=FakeUser32()
        with patch.object(win,"_require_windows",return_value=None), \
             patch.object(win,"_window_info",return_value=self.main), \
             patch.object(win,"_foreground_owned_browser_find_popup",
                          return_value=self.main) as popup, \
             patch.object(win,"_foreground_or_refocus",
                          side_effect=AssertionError("Wrong focus policy")) as normal, \
             patch.object(win,"_press_hotkey"):
            win.execute("windows_hotkey",{"hwnd":88,"keys":"ENTER"})
            win.execute("windows_hotkey",{"hwnd":88,"keys":"ESC"})
            self.assertEqual(popup.call_count,2)
            normal.assert_not_called()

    def test_browser_ctrl_f_still_requires_strict_original_window(self):
        with patch.object(win,"_require_windows",return_value=None), \
             patch.object(win,"_window_info",return_value=self.main), \
             patch.object(win,"_foreground_owned_browser_find_popup",
                          side_effect=AssertionError("No popup exception")) as popup, \
             patch.object(win,"_foreground_or_refocus",return_value=self.main) as normal, \
             patch.object(win,"_press_hotkey"):
            win.execute("windows_hotkey",{"hwnd":88,"keys":"CTRL+F"})
            normal.assert_called_once_with(88)
            popup.assert_not_called()


if __name__=="__main__":
    unittest.main()
