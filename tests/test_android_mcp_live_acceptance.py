"""Offline contract tests of the *manual* real Windows Android MCP gate.

No device or emulator starts during ordinary CI. A successful real gate has
to come from a separate Windows self-hosted workflow with an existing AVD.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import android_mcp_bridge
from scripts.android_mcp_live_acceptance import (
    accept, validate_inputs, adb_read, exact_avd_name
)

SERIAL="emulator-5554"
AVD="LHM_CI_Test"
NODE={"index":1,"role":"button","hint":"back",
      "bounds":{"x":120,"y":220,"width":80,"height":40}}
SAFE_UI={"privacy":"geometry-only","read_only":True,
         "totalNodes":2,"elements":[NODE]}


class LiveAndroidMcpGateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="lhm-real-android-gate-")
        self.root=Path(self.temp.name)
        (self.root/"ggminh"/"bin").mkdir(parents=True)
        (self.root/"ggminh"/"bin"/"lhm-android-mcp.js").write_text("// fixture")
        self.node=self.root/"node.exe"
        self.node.write_text("fixture")
        self.env=patch.dict(os.environ,{},clear=False)
        self.env.start()
        self.native=patch.object(android_mcp_bridge,"native_desktop",return_value=True)
        self.native.start()
        self.node_path=patch("scripts.android_mcp_live_acceptance.shutil.which",
                             return_value=str(self.node))
        self.node_path.start()
        self.calls=[]

    def tearDown(self):
        self.node_path.stop()
        self.native.stop()
        self.env.stop()
        self.temp.cleanup()

    def adb(self,serial,*args):
        self.calls.append(("adb",serial,args))
        if args==("emu","avd","name"):
            return AVD+"\r\nOK\r\n"
        if args==("shell","getprop","sys.boot_completed"):
            return "1\n"
        raise AssertionError("Unexpected ADB action: "+str(args))

    async def tools(self,role,tool,args):
        self.calls.append(("mcp",role,tool,args))
        self.assertEqual(role,"research")
        if tool=="android_mcp_devices":
            return {"devices":[{"serial":SERIAL,"kind":"emulator","status":"device"}],
                    "read_only":True}
        if tool=="android_mcp_inspect":
            return dict(SAFE_UI)
        raise AssertionError("Non-read-only tool was invoked")

    def run_gate(self,execute_fn=None,adb_fn=None,serial=SERIAL,avd=AVD):
        return asyncio.run(accept(serial,avd,self.root/"ggminh",platform="win32",
            adb_fn=adb_fn or self.adb,execute_fn=execute_fn or self.tools))

    def test_full_read_only_fixture_success_does_not_claim_live_hardware(self):
        result=self.run_gate()
        self.assertEqual(result["status"],"PASS")
        self.assertEqual(result["permissions"],"read-only")
        self.assertEqual(result["source"],"live-windows-avd")
        self.assertFalse(result["actual_android_tap_tested"])
        self.assertFalse(result["tap_enabled"])
        self.assertEqual(result["interactive_elements"],1)
        self.assertEqual([x[2] for x in self.calls if x[0]=="mcp"],
                         ["android_mcp_devices","android_mcp_inspect"])
        self.assertEqual(len([x for x in self.calls if x[0]=="adb"]),2)
        self.assertEqual(os.environ["LHM_ANDROID_MCP_TAP_ENABLED"],"0")
        # Summary excludes serial, UI text, secrets, file paths and tokens.
        summary=json.dumps(result)
        for secret in (SERIAL,AVD,"password","observation_id",str(self.root)):
            self.assertNotIn(secret,summary)

    def test_wrong_avd_and_not_booted_fail_before_mcp(self):
        with self.assertRaisesRegex(ValueError,"different AVD"):
            self.run_gate(avd="Another_Test")
        self.assertFalse(any(x[0]=="mcp" for x in self.calls))
        self.calls.clear()
        def not_booted(serial,*args):
            result=self.adb(serial,*args)
            return "0" if args==("shell","getprop","sys.boot_completed") else result
        with self.assertRaisesRegex(ValueError,"not completed"):
            self.run_gate(adb_fn=not_booted)
        self.assertFalse(any(x[0]=="mcp" for x in self.calls))

    def test_wrong_device_and_ui_information_leak_fail_closed(self):
        async def wrong_device(role,tool,args):
            if tool=="android_mcp_devices":
                return {"devices":[{"serial":"emulator-5556","kind":"emulator","status":"device"}]}
            raise AssertionError("Read UI after wrong device")
        with self.assertRaisesRegex(ValueError,"exactly the approved"):
            self.run_gate(execute_fn=wrong_device)
        self.calls.clear()
        async def leaked(role,tool,args):
            if tool=="android_mcp_devices":
                return await self.tools(role,tool,args)
            return {**SAFE_UI,"text":"private user@example.com"}
        with self.assertRaisesRegex(ValueError,"unexpected UI metadata"):
            self.run_gate(execute_fn=leaked)
        self.assertFalse(any(x[2]=="android_mcp_tap" for x in self.calls
                             if x[0]=="mcp"))

    def test_no_unsafe_serial_or_avd_or_non_windows(self):
        for serial,avd in [
            ("device-1234",AVD),
            ("emulator-5554;bad",AVD),
            (SERIAL,"../secrets"),
            (SERIAL,"LHM CI Test"),
        ]:
            with self.assertRaises(ValueError):
                validate_inputs(serial,avd,self.root/"ggminh",platform="win32")
        with self.assertRaisesRegex(ValueError,"requires Windows"):
            validate_inputs(SERIAL,AVD,self.root/"ggminh",platform="posix")
        with self.assertRaises(ValueError):
            validate_inputs(SERIAL,AVD,self.root/"missing",platform="win32")

    def test_adb_helper_rejects_writes_before_spawning(self):
        for cmd in [
            ("shell","input","tap","1","1"),
            ("shell","am","start","com.android.settings"),
            ("install","my.apk"),
        ]:
            with self.assertRaisesRegex(ValueError,"Unsupported ADB read"):
                adb_read(SERIAL,*cmd)
        self.assertEqual(exact_avd_name("LHM_CI_Test\nOK\n"),AVD)
        for data in ("Another_Test\nNO",AVD,AVD+"\nOK\nsecret"):
            with self.assertRaises(ValueError):
                exact_avd_name(data)


if __name__=="__main__":
    unittest.main()
