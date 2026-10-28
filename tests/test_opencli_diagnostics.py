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

    async def test_profile_picker_only_returns_connected_valid_ids(self):
        class Fake:
            returncode=0
            async def communicate(self):
                return json.dumps({"ok":True,"data":{
                    "daemonRunning":True,
                    "profiles":[
                        {"contextId":"lhm_context_01","alias":"LHM","connected":True},
                        {"contextId":"personal_context","alias":"personal","connected":False},
                        {"contextId":"bad:profile","alias":"bad","connected":True}
                    ]}}).encode(),b""
        with patch.dict(os.environ,{"LHM_DESKTOP_NATIVE":"1"}):
            with patch.object(diag,"_windows",return_value=True):
                with patch.object(diag,"_toolchain",return_value=("node.exe","main.js")):
                    with patch.object(diag.asyncio,"create_subprocess_exec",return_value=Fake()):
                        result=await diag.connected_profiles()
        self.assertEqual(result["profiles"],[{"id":"lhm_context_01","label":"LHM"}])
        self.assertNotIn("personal",str(result))

    async def test_profile_picker_requires_native_controller_token(self):
        from fastapi.testclient import TestClient
        import computer_executor
        api=TestClient(computer_executor.runner)
        with patch.dict(os.environ,{"LHM_COMPUTER_RUNNER_TOKEN":"owner-test-token"}):
            self.assertEqual(api.get("/v1/opencli/profiles").status_code,401)
            with patch.object(diag,"connected_profiles",return_value={
                "cli_installed":True,"profiles":[{"id":"lhm_context_01","label":"LHM"}],
                "detail":"Choose dedicated profile"}):
                result=api.get("/v1/opencli/profiles",
                    headers={"Authorization":"Bearer owner-test-token"})
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json()["profiles"][0]["id"],"lhm_context_01")

    async def test_owner_click_starts_stopped_bridge_before_profile_list(self):
        calls=[]
        class Fake:
            returncode=0
            def __init__(self, data): self.data=data
            async def communicate(self):
                return json.dumps({"ok":True,"data":self.data}).encode(),b""
        async def spawn(*argv,**opts):
            calls.append((argv,opts))
            if argv[2:]==("profile","list") and len(calls)==1:
                return Fake({"daemonRunning":False,"profiles":[]})
            if argv[2:]==("daemon","restart"):
                return Fake({"action":"started","status":{"running":True}})
            if argv[2:]==("profile","list") and len(calls)==3:
                return Fake({"daemonRunning":True,"profiles":[
                    {"contextId":"lhm_context_01","connected":True}]})
            raise AssertionError(argv)
        with patch.dict(os.environ,{"LHM_DESKTOP_NATIVE":"1",
            "OPENROUTER_API_KEY":"TOP_SECRET","LHM_COMPUTER_RUNNER_TOKEN":"TOP_SECRET"}):
            with patch.object(diag,"_windows",return_value=True):
                with patch.object(diag,"_toolchain",return_value=("node.exe","main.js")):
                    with patch.object(diag.asyncio,"create_subprocess_exec",side_effect=spawn):
                        result=await diag.connected_profiles()
        self.assertTrue(result["daemon_started"])
        self.assertEqual([x[0][2:] for x in calls],
            [("profile","list"),("daemon","restart"),("profile","list")])
        self.assertEqual(result["profiles"],[{"id":"lhm_context_01","label":"lhm_context_01"}])
        for _,opts in calls:
            self.assertNotIn("OPENROUTER_API_KEY",opts["env"])
            self.assertNotIn("LHM_COMPUTER_RUNNER_TOKEN",opts["env"])

    async def test_owner_click_never_restarts_already_running_bridge(self):
        calls=[]
        class Fake:
            returncode=0
            async def communicate(self):
                return json.dumps({"ok":True,"data":{
                    "daemonRunning":True,"profiles":[{"contextId":"lhm_context_01",
                    "connected":True}]}}).encode(),b""
        async def spawn(*argv,**opts):
            calls.append(argv)
            return Fake()
        with patch.dict(os.environ,{"LHM_DESKTOP_NATIVE":"1"}):
            with patch.object(diag,"_windows",return_value=True):
                with patch.object(diag,"_toolchain",return_value=("node.exe","main.js")):
                    with patch.object(diag.asyncio,"create_subprocess_exec",side_effect=spawn):
                        result=await diag.connected_profiles()
        self.assertFalse(result["daemon_started"])
        self.assertEqual([a[2:] for a in calls],[("profile","list")])

    async def test_disabled_never_launches_process(self):
        with patch.dict(os.environ,{"LHM_DESKTOP_NATIVE":"1","LHM_OPENCLI_ENABLED":"0"}):
            with patch.object(diag,"_windows",return_value=True):
                with patch.object(diag.asyncio,"create_subprocess_exec") as proc:
                    status=await diag.inspect()
                    self.assertFalse(status["ready"])
                    proc.assert_not_called()


if __name__=="__main__":
    unittest.main()
