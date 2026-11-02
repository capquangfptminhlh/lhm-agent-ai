"""REAL subprocess interoperability test: Python LHM Agent -> ggminh Node MCP.

Unlike mocked bridge tests, this spawns the actual ggminh stdio server.
No emulator, ADB device, screenshot, gesture or account is required.
The only allowlisted serial is an impossible CI-only sentinel and only
the *read* MCP tools may be called. This is NOT live Android acceptance.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
import android_mcp_bridge as bridge

# A literal non-device sentinel: not a USB/ADB serial or emulator-XXXX.
IMPOSSIBLE_SERIAL="lhm-ci-no-real-device"


@unittest.skipUnless(sys.platform=="win32" and
                     os.getenv("LHM_TEST_GGMINH_DIR"),
                     "Only the explicit Windows real-node integration CI runs this")
class RealNodeMcpInterop(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        root=Path(os.environ["LHM_TEST_GGMINH_DIR"]).resolve()
        self.entry=root/"bin"/"lhm-android-mcp.js"
        self.assertTrue(self.entry.is_file(), "Checked-out ggminh stdio server is missing")
        self.assertFalse(self.entry.is_symlink(), "MCP entry must be a real file")
        self.node=shutil.which("node")
        self.assertTrue(self.node and Path(self.node).is_file(),
                        "Node.js for MCP stdio not installed")
        self.config=patch.dict(os.environ,{
            "LHM_DESKTOP_NATIVE":"1",
            "LHM_ANDROID_MCP_ENABLED":"1",
            "LHM_ANDROID_MCP_TAP_ENABLED":"0",
            "LHM_ANDROID_MCP_NODE":self.node,
            "LHM_ANDROID_MCP_ENTRY":str(self.entry),
            "LHM_ANDROID_MCP_ALLOWED_SERIALS":IMPOSSIBLE_SERIAL,
            "LHM_ANDROID_MCP_ALLOWED_ROLES":"research",
            "OPENROUTER_API_KEY":"DONT_PASS_CI_SENTINEL_TO_MCP"
        })
        self.config.start()
        self.win=patch.object(bridge,"native_desktop",return_value=True)
        self.win.start()

    async def asyncTearDown(self):
        self.win.stop()
        self.config.stop()

    async def test_real_node_mcp_handshake_returns_only_denied_devices(self):
        self.assertTrue(bridge.configured("research"))
        actual=await bridge.execute("research","android_mcp_devices",{})
        # Node really started, negotiated the 2025-06-18 MCP protocol and
        # returned one parsed tools/call result. CI sentinel matches no ADB.
        self.assertEqual(actual,{"devices":[],"read_only":True})
        with self.assertRaises(HTTPException) as wrong_role:
            await bridge.execute("quality","android_mcp_devices",{})
        self.assertEqual(wrong_role.exception.status_code,403)
        with self.assertRaises(HTTPException) as wrong_serial:
            await bridge.execute("research","android_mcp_inspect",
                                 {"serial":"emulator-5554"})
        self.assertEqual(wrong_serial.exception.status_code,403)
        with self.assertRaises(HTTPException) as blocked_write:
            await bridge.execute("research","android_mcp_tap",
                {"serial":IMPOSSIBLE_SERIAL,"observation_id":"0"*32,"index":1},
                run_id="12345678-1234-4123-8123-123456789abc")
        self.assertEqual(blocked_write.exception.status_code,403)

    async def test_server_exposes_exact_read_only_tools_and_denies_tap_rpc(self):
        messages=[
            {"jsonrpc":"2.0","id":1,"method":"initialize",
             "params":{"protocolVersion":"2025-06-18","capabilities":{},
                       "clientInfo":{"name":"lhm-interoperability","version":"1"}}},
            {"jsonrpc":"2.0","method":"notifications/initialized"},
            {"jsonrpc":"2.0","id":2,"method":"tools/list"},
            {"jsonrpc":"2.0","id":3,"method":"tools/call",
             "params":{"name":"android_mcp_tap","arguments":{}}},
        ]
        payload=("\n".join(json.dumps(m,separators=(",",":")) for m in messages)+"\n").encode()
        env={
            key:os.environ[key] for key in
            ("SYSTEMROOT","WINDIR","PATH","PATHEXT","TEMP","TMP",
             "APPDATA","LOCALAPPDATA","USERPROFILE","ANDROID_SDK_ROOT","ANDROID_HOME")
            if key in os.environ
        }
        env["LHM_MCP_ALLOWED_SERIALS"]=IMPOSSIBLE_SERIAL
        proc=await asyncio.create_subprocess_exec(
            self.node,str(self.entry),
            stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,env=env)
        try:
            output,_=await asyncio.wait_for(proc.communicate(payload),timeout=18)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            self.fail("Actual Node MCP server did not exit/return after real stdio input")
        self.assertEqual(proc.returncode,0)
        self.assertLess(len(output),96000)
        replies=[json.loads(line) for line in output.decode().splitlines() if line.strip()]
        self.assertEqual([r.get("id") for r in replies],[1,2,3])
        self.assertEqual(replies[0]["result"]["protocolVersion"],"2025-06-18")
        tools=replies[1]["result"]["tools"]
        names={tool["name"] for tool in tools}
        self.assertEqual(names,{
            "android_capabilities","android_list_devices",
            "android_list_avds","android_inspect_ui"})
        self.assertTrue(all(t.get("annotations",{}).get("readOnlyHint") is True
                            for t in tools))
        self.assertTrue(replies[2]["result"]["isError"])
        self.assertNotIn("DONT_PASS_CI_SENTINEL_TO_MCP",str(replies))
        self.assertNotIn("shell",names)
        self.assertNotIn("android_mcp_tap",names)


if __name__=="__main__":
    unittest.main()
