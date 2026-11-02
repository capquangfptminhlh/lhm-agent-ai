"""Android MCP role/serial safety and stdio client tests (no Android hardware)."""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException

import android_mcp_bridge as bridge


class FakeProcess:
    returncode=0
    def __init__(self,result):
        self.result=result
        self.received=None
    async def communicate(self,data=None):
        self.received=data
        replies=[
            {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-06-18"}},
            {"jsonrpc":"2.0","id":2,"result":{"isError":False,
                "content":[{"type":"text","text":json.dumps(self.result)}]}}
        ]
        return ("\n".join(json.dumps(r) for r in replies)+"\n").encode(),b""
    def kill(self):pass


class AndroidBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.entry=self.root/"bin"/"lhm-android-mcp.js"
        self.entry.parent.mkdir()
        self.entry.write_text("// local MCP fixture",encoding="utf-8")
        self.config={
            "LHM_DESKTOP_NATIVE":"1",
            "LHM_ANDROID_MCP_ENABLED":"1",
            "LHM_ANDROID_MCP_ENTRY":str(self.entry),
            "LHM_ANDROID_MCP_NODE":"node",
            "LHM_ANDROID_MCP_ALLOWED_SERIALS":"emulator-5554",
            "LHM_ANDROID_MCP_ALLOWED_ROLES":"research,quality",
            "OPENROUTER_API_KEY":"NEVER_FORWARD_TO_MCP",
        }
        self.env=patch.dict(os.environ,self.config,clear=True)
        self.env.start()
        self.native=patch.object(bridge,"native_desktop",return_value=True)
        self.native.start()

    def tearDown(self):
        self.native.stop()
        self.env.stop()
        self.tmp.cleanup()

    def test_roles_serials_and_default_denial(self):
        self.assertTrue(bridge.configured("quality"))
        self.assertTrue(bridge.configured("research"))
        self.assertFalse(bridge.configured("ads"))
        bridge.validate("research","android_mcp_devices",{})
        bridge.validate("quality","android_mcp_inspect",{"serial":"emulator-5554"})
        for role,tool,args in [
            ("ads","android_mcp_devices",{}),
            ("research","android_mcp_inspect",{"serial":"emulator-5556"}),
            ("research","android_mcp_inspect",{"serial":"emulator-5554","shell":"reboot"}),
            ("research","android_mcp_tap",{"serial":"emulator-5554"}),
        ]:
            with self.assertRaises(HTTPException):
                bridge.validate(role,tool,args)
        os.environ["LHM_ANDROID_MCP_ENABLED"]="0"
        self.assertFalse(bridge.configured("research"))
        with self.assertRaises(HTTPException):
            bridge.validate("research","android_mcp_devices",{})

    def test_double_redaction_removes_sensitive_fields(self):
        leak="OTP 123456 user@example.com"
        raw={"totalNodes":1,"secrets":leak,"elements":[
            {"index":1,"type":"Button","role":"button","hint":"search",
             "label":leak,"rawText":leak,
             "bounds":{"x":10,"y":20,"width":80,"height":40,"left":0,"top":0}}
        ]}
        safe=bridge.clean_result("android_mcp_inspect",raw)
        self.assertEqual(safe["elements"][0]["role"],"button")
        self.assertEqual(safe["elements"][0]["hint"],"search")
        self.assertNotIn(leak,json.dumps(safe))
        self.assertNotIn("left",json.dumps(safe))
        devices=bridge.clean_result("android_mcp_devices",{"devices":[
            {"serial":"emulator-5554","status":"device","kind":"emulator","model":leak},
            {"serial":"other-phone","status":"device","kind":"physical","model":leak}
        ]})
        self.assertEqual(len(devices["devices"]),1)
        self.assertNotIn(leak,json.dumps(devices))

    def test_stdio_subprocess_is_fixed_and_has_no_provider_key(self):
        process=FakeProcess({"devices":[{
            "serial":"emulator-5554","status":"device","kind":"emulator",
            "name":"private-data"
        }]})
        seen={}
        async def spawn(*args,**kwargs):
            seen.update({"argv":args,**kwargs})
            return process
        with patch.object(bridge.asyncio,"create_subprocess_exec",side_effect=spawn):
            result=asyncio.run(bridge.execute("research","android_mcp_devices",{}))
        self.assertEqual(result["devices"][0]["serial"],"emulator-5554")
        self.assertEqual(seen["argv"],("node",str(self.entry)))
        self.assertEqual(seen["env"]["LHM_MCP_ALLOWED_SERIALS"],"emulator-5554")
        self.assertNotIn("OPENROUTER_API_KEY",seen["env"])
        self.assertNotIn("LHM_COMPUTER_RUNNER_TOKEN",seen["env"])
        self.assertEqual(len(process.received.splitlines()),3)
        request=json.loads(process.received.splitlines()[-1])
        self.assertEqual(request["params"]["name"],"android_list_devices")

    def test_observation_token_is_first_in_bounded_computer_history(self):
        # A full page can have 120 nodes; the Computer LLM only sees
        # 1800 chars of previous results. Token must be ahead of elements.
        text="irrelevant private text should not be exposed"
        elements=[{"index":i,"role":"button","hint":"back",
                   "bounds":{"x":i,"y":i,"width":100,"height":50}}
                  for i in range(1,121)]
        fake=FakeProcess({"totalNodes":120,"elements":elements,
                          "privacy":"geometry-only","secret":text})
        async def spawn(*args,**kwargs):return fake
        with patch.object(bridge,"native_desktop",return_value=True), \\
             patch.object(bridge.asyncio,"create_subprocess_exec",side_effect=spawn), \\
             patch.dict(os.environ,{"LHM_ANDROID_MCP_TAP_ENABLED":"1"}), \\
             patch("android_mcp_actions.prepare",
                   return_value="a"*32):
            result=asyncio.run(bridge.execute("research","android_mcp_inspect",
                {"serial":"emulator-5554"},run_id="12345678-1234-4123-8123-123456789abc"))
        self.assertEqual(list(result)[:2],["observation_id","expires_in_seconds"])
        summary=str(result)[:1800]
        self.assertIn("a"*32,summary)
        self.assertNotIn(text,str(result))
        self.assertEqual(len(result["elements"]),120)

    def test_no_config_and_bad_response_fail_closed(self):
        fake=FakeProcess({"devices":[]})
        fake.returncode=1
        async def spawn(*args,**kwargs):return fake
        with patch.object(bridge.asyncio,"create_subprocess_exec",side_effect=spawn):
            with self.assertRaises(HTTPException):
                asyncio.run(bridge.execute("quality","android_mcp_devices",{}))
        os.environ["LHM_ANDROID_MCP_ALLOWED_SERIALS"]=""
        self.assertFalse(bridge.configured("quality"))


if __name__=="__main__":
    unittest.main()
