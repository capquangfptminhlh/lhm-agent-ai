"""Inspect owner-selected OpenCLI Bridge state without touching Chrome."""
import asyncio
import json
import os
import unittest
from unittest.mock import patch

import opencli_diagnostics as diag


class DiagnosticTests(unittest.IsolatedAsyncioTestCase):
    async def test_disconnected_profile_is_not_marked_ready(self):
        class Fake:
            returncode=0
            async def communicate(self):
                d={"ok":True,"data":{"daemonRunning":True,"daemonStale":False,
                   "profiles":[{"alias":"lhm-work","connected":False},
                               {"alias":"personal","connected":True}]}}
                return json.dumps(d).encode(),b""
        with patch.dict(os.environ,{"LHM_DESKTOP_NATIVE":"1",
            "LHM_OPENCLI_ENABLED":"1","LHM_OPENCLI_PROFILE":"lhm-work"}):
            with patch.object(diag,"_windows",return_value=True):
                with patch.object(diag,"_configuration",return_value=("node","app.js","lhm-work")):
                    with patch.object(diag.asyncio,"create_subprocess_exec",return_value=Fake()):
                        status=await diag.inspect()
        self.assertTrue(status["cli_installed"])
        self.assertTrue(status["bridge_running"])
        self.assertFalse(status["profile_connected"])
        self.assertFalse(status["ready"])
        self.assertNotIn("personal",str(status))

    async def test_correct_profile_connected_is_ready(self):
        class Fake:
            returncode=0
            async def communicate(self):
                d={"ok":True,"data":{"daemonRunning":True,"daemonStale":False,
                    "profiles":[{"alias":"lhm-work","connected":True}]}}
                return json.dumps(d).encode(),b""
        with patch.dict(os.environ,{"LHM_DESKTOP_NATIVE":"1",
            "LHM_OPENCLI_ENABLED":"1","LHM_OPENCLI_PROFILE":"lhm-work"}):
            with patch.object(diag,"_windows",return_value=True):
                with patch.object(diag,"_configuration",return_value=("node","app.js","lhm-work")):
                    with patch.object(diag.asyncio,"create_subprocess_exec",return_value=Fake()):
                        status=await diag.inspect()
        self.assertTrue(status["ready"])

    async def test_disabled_never_launches_process(self):
        with patch.dict(os.environ,{"LHM_DESKTOP_NATIVE":"1","LHM_OPENCLI_ENABLED":"0"}):
            with patch.object(diag,"_windows",return_value=True):
                with patch.object(diag.asyncio,"create_subprocess_exec") as proc:
                    status=await diag.inspect()
                    self.assertFalse(status["ready"])
                    proc.assert_not_called()


if __name__=="__main__":
    unittest.main()
