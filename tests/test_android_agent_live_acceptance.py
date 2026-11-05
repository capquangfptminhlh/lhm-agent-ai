"""Synthetic harness tests only. Live Windows self-hosted AVD is separate."""
from __future__ import annotations

import struct
import unittest

from scripts.android_agent_live_acceptance import (
    configured_avd, screenshot_metadata, run_live, PNG_SIGNATURE,
)


class FakeHost:
    def __init__(self, serial="emulator-5554"):
        self.serial=serial
        self.calls=[]
    def start(self, avd):
        self.calls.append("start")
    def wait_boot(self):
        self.calls.append("boot")
        return self.serial
    def open_settings(self, serial):
        self.calls.append("settings")
    def screenshot(self, serial):
        self.calls.append("screenshot")
        return {"width":720,"height":1280}
    def stop(self):
        self.calls.append("stop")


class FakeBridge:
    def __init__(self, *, safe_back=True, raw_text=False, deny=False):
        self.safe_back=safe_back
        self.raw_text=raw_text
        self.deny=deny
        self.calls=[]
    async def execute(self, role, tool, args, run_id=None):
        self.calls.append((tool,run_id))
        if tool=="android_mcp_devices":
            return {"devices":[{"serial":"emulator-5554","status":"device"}]}
        if tool=="android_mcp_inspect":
            node={"index":1,"role":"button",
                  "hint":"back" if self.safe_back else None,
                  "bounds":{"x":60,"y":80,"width":30,"height":30}}
            if self.raw_text:node["text"]="Secret OTP 12345"
            return {"privacy":"geometry-only",
                    "read_only":True,"elements":[node],
                    "observation_id":"abcdef"*5+"ab" if run_id else None}
        if tool=="android_mcp_tap":
            if self.deny:raise RuntimeError("No approved target")
            assert args["serial"]=="emulator-5554"
            assert args["index"]==1
            assert run_id is not None
            return {"state":"tap_sent","confirmed_by_cli":True,
                    "requires_owner_reconciliation":True,
                    "external_effect_verified":False}
        raise AssertionError("Unexpected MCP tool")


class TestWindowsAvdHarness(unittest.IsolatedAsyncioTestCase):
    async def test_reads_actual_bridge_interface_and_cleans_up(self):
        host=FakeHost()
        bridge=FakeBridge()
        output=await run_live(host,bridge,"LHM_Test_Pixel")
        self.assertEqual(output["runtime"],"REAL_WINDOWS_AVD")
        self.assertEqual(output["read"],"PASS")
        self.assertEqual(output["tap"],"NOT_REQUESTED")
        self.assertFalse(output["external_effect_verified"])
        self.assertEqual([c[0] for c in bridge.calls],
                         ["android_mcp_devices","android_mcp_inspect"])
        self.assertEqual(host.calls[-1],"stop")

    async def test_explicit_safe_back_tap_is_bound_and_unverified(self):
        host=FakeHost()
        bridge=FakeBridge()
        result=await run_live(host,bridge,"LHM_Test_Pixel",
                              allow_back_tap=True)
        self.assertEqual(result["tap"],"SENT_UNVERIFIED_NEEDS_OWNER_RECONCILIATION")
        self.assertFalse(result["external_effect_verified"])
        self.assertEqual(bridge.calls[-1][0],"android_mcp_tap")
        self.assertEqual(bridge.calls[-1][1],bridge.calls[1][1])
        self.assertEqual(host.calls[-1],"stop")

    async def test_no_back_widget_never_taps(self):
        host=FakeHost()
        bridge=FakeBridge(safe_back=False)
        with self.assertRaises(RuntimeError):
            await run_live(host,bridge,"LHM_Test_Pixel",
                           allow_back_tap=True)
        self.assertEqual([c[0] for c in bridge.calls],
                         ["android_mcp_devices","android_mcp_inspect"])
        self.assertEqual(host.calls[-1],"stop")

    async def test_safe_back_rejects_offscreen_or_ambiguous_index(self):
        class UnsafeBridge(FakeBridge):
            def __init__(self, problem):
                super().__init__()
                self.problem=problem

            async def execute(self, role, tool, args, run_id=None):
                data=await super().execute(role,tool,args,run_id=run_id)
                if tool=="android_mcp_inspect":
                    if self.problem=="offscreen":
                        data["elements"][0]["bounds"]["x"]=999
                    else:
                        data["elements"].append({
                            "index":1,"role":"toggle","hint":None,
                            "bounds":{"x":100,"y":120,"width":20,"height":20}
                        })
                return data

        for issue in ("offscreen","duplicate"):
            with self.subTest(issue=issue):
                host=FakeHost()
                bridge=UnsafeBridge(issue)
                with self.assertRaises(RuntimeError):
                    await run_live(host,bridge,"LHM_Test_Pixel",
                                   allow_back_tap=True)
                self.assertEqual(
                    [call[0] for call in bridge.calls],
                    ["android_mcp_devices","android_mcp_inspect"])
                self.assertEqual(host.calls[-1],"stop")

    async def test_private_label_refusal_and_cleanup(self):
        host=FakeHost()
        with self.assertRaises(RuntimeError):
            await run_live(host,FakeBridge(raw_text=True),"LHM_Test_Pixel")
        self.assertEqual(host.calls[-1],"stop")

    async def test_bad_target_never_executes_android(self):
        host=FakeHost(serial="PHONE123")
        bridge=FakeBridge()
        with self.assertRaises(RuntimeError):
            await run_live(host,bridge,"LHM_Test_Pixel",allow_back_tap=True)
        self.assertEqual(bridge.calls,[])
        self.assertEqual(host.calls[-1],"stop")

    async def test_tap_denied_does_not_get_reported_as_pass(self):
        host=FakeHost()
        with self.assertRaises(RuntimeError):
            await run_live(host,FakeBridge(deny=True),"LHM_Test_Pixel",
                           allow_back_tap=True)
        self.assertEqual(host.calls[-1],"stop")


class TestLivePreflight(unittest.TestCase):
    def test_avd_name_restrictions(self):
        self.assertEqual(configured_avd("LHM_Test_Pixel"),"LHM_Test_Pixel")
        for invalid in ("../escape","AVD;whoami","","A"*70,"name with spaces"):
            with self.assertRaises(RuntimeError):
                configured_avd(invalid)

    def test_png_header_and_dimensions(self):
        payload=PNG_SIGNATURE+b"\x00\x00\x00\rIHDR"+struct.pack(">II",720,1280)
        self.assertEqual(screenshot_metadata(payload),
                         {"width":720,"height":1280})
        for wrong in (b"",b"\x00"*32,PNG_SIGNATURE+b"\x00\x00\x00\rIHDR"+
                      struct.pack(">II",0,1280)):
            with self.assertRaises(RuntimeError):
                screenshot_metadata(wrong)


if __name__=="__main__":
    unittest.main()
