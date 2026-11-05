"""Real MCP status checker: strictly read-only and native-owner authenticated.

All ADB connections in ordinary CI are replaced with controlled fixtures.
Actual device connectivity is proved only by running this endpoint on Windows.
"""
import asyncio
import os
import unittest
from unittest.mock import AsyncMock,patch
from fastapi.testclient import TestClient

import android_mcp_diagnostics as diagnostics
import computer_executor as runner

class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.environment=patch.dict(os.environ,{
            "LHM_ANDROID_MCP_ENABLED":"1",
            "LHM_ANDROID_MCP_TAP_ENABLED":"1",
            "LHM_ANDROID_MCP_ALLOWED_ROLES":"research,quality",
            "LHM_ANDROID_MCP_ALLOWED_SERIALS":"emulator-5554,ABC123",
            "LHM_COMPUTER_RUNNER_TOKEN":"test-local-only"
        },clear=False)
        self.environment.start()
        self.auth={"Authorization":"Bearer test-local-only"}
        self.client=TestClient(runner.runner)

    def tearDown(self):
        self.environment.stop()

    def probe(self,respond):
        with patch.object(diagnostics,"configured",side_effect=lambda role:role=="quality"), \
             patch.object(diagnostics,"execute",side_effect=respond) as bridge:
            result=asyncio.run(diagnostics.inspect_status())
        return result,bridge

    @staticmethod
    async def healthy(role,tool,args,run_id=None):
        assert role=="quality"
        assert run_id is None  # Never return tap-authorization tokens.
        if tool=="android_mcp_devices":
            assert args=={}
            return {"read_only":True,"devices":[
                {"serial":"ABC123","status":"device","kind":"physical","model":"Private Account"},
                {"serial":"emulator-5554","status":"device","kind":"emulator","content":"OTP 123456"},
                {"serial":"other-phone","status":"device","kind":"physical","password":"secret"}]}
        assert tool=="android_mcp_inspect" and args=={"serial":"emulator-5554"}
        return {"privacy":"geometry-only","read_only":True,
                "totalNodes":9,
                "elements":[{"index":1,"role":"button","hint":"back",
                             "bounds":{"x":10,"y":20,"width":50,"height":40},
                             "rawText":"Private account OTP 123456"}]}

    def test_real_read_path_reports_both_mcp_and_avd(self):
        result,bridge=self.probe(self.healthy)
        self.assertTrue(result["ready"])
        self.assertTrue(result["configured"])
        self.assertTrue(result["connectionChecked"])
        self.assertTrue(result["mcpConnected"])
        self.assertTrue(result["avdConnected"])
        self.assertTrue(result["uiReadable"])
        self.assertEqual(result["uiActionableCount"],1)
        self.assertTrue(result["tapEnabled"])
        self.assertEqual(result["detail"],"avd-ui-read-confirmed")
        self.assertEqual(bridge.await_count,2)
        self.assertEqual({d["serial"] for d in result["devices"]},
                         {"ABC123","emulator-5554"})
        rendered=str(result)
        self.assertNotIn("Private",rendered)
        self.assertNotIn("OTP",rendered)
        self.assertNotIn("other-phone",rendered)
        self.assertNotIn("bounds",rendered)
        self.assertNotIn("observation_id",rendered)

    def test_disabled_does_not_call_adb(self):
        os.environ["LHM_ANDROID_MCP_ENABLED"]="0"
        async def fail(*args,**kwargs):
            raise AssertionError("No MCP subprocess should be created")
        result,bridge=self.probe(fail)
        self.assertFalse(result["ready"])
        self.assertFalse(result["connectionChecked"])
        self.assertFalse(result["mcpConnected"])
        self.assertEqual(result["detail"],"disabled")
        bridge.assert_not_called()

    def test_online_physical_but_no_test_avd_fails_without_inspect(self):
        async def only_phone(role,tool,args,run_id=None):
            self.assertEqual(tool,"android_mcp_devices")
            return {"devices":[{"serial":"ABC123","status":"device","kind":"physical"}]}
        result,bridge=self.probe(only_phone)
        self.assertTrue(result["mcpConnected"])
        self.assertFalse(result["avdConnected"])
        self.assertFalse(result["ready"])
        self.assertEqual(result["detail"],"no-online-allowed-avd")
        self.assertEqual(bridge.await_count,1)

    def test_emulator_offline_reports_no_avd(self):
        async def offline(role,tool,args,run_id=None):
            self.assertEqual(tool,"android_mcp_devices")
            return {"devices":[{"serial":"emulator-5554","status":"offline","kind":"emulator"}]}
        result,bridge=self.probe(offline)
        self.assertFalse(result["avdConnected"])
        self.assertEqual(bridge.await_count,1)

    def test_bad_mcp_reply_and_secret_error_not_leaked(self):
        async def bad(role,tool,args,run_id=None):
            raise RuntimeError("SECRET_PASSWORD_ABCD local stderr")
        result,_=self.probe(bad)
        self.assertEqual(result["detail"],"mcp-device-read-failed")
        self.assertNotIn("SECRET_PASSWORD_ABCD",str(result))
        async def invalid(role,tool,args,run_id=None):
            return {"devices":"not-an-array","text":"OTP 123456"}
        result,_=self.probe(invalid)
        self.assertEqual(result["detail"],"invalid-mcp-device-response")

    def test_mcp_ui_failure_is_not_reported_as_ready(self):
        async def fail_ui(role,tool,args,run_id=None):
            if tool=="android_mcp_devices":
                return {"devices":[{"serial":"emulator-5554","status":"device","kind":"emulator"}]}
            raise RuntimeError("Private UI dump not allowed")
        result,bridge=self.probe(fail_ui)
        self.assertTrue(result["avdConnected"])
        self.assertFalse(result["uiReadable"])
        self.assertFalse(result["ready"])
        self.assertEqual(result["detail"],"mcp-ui-read-failed")
        self.assertEqual(bridge.await_count,2)

    def test_readable_ui_without_actionable_nodes_not_pass(self):
        async def empty_ui(role,tool,args,run_id=None):
            if tool=="android_mcp_devices":
                return {"devices":[{"serial":"emulator-5554","status":"device","kind":"emulator"}]}
            return {"privacy":"geometry-only","read_only":True,"totalNodes":3,"elements":[]}
        result,_=self.probe(empty_ui)
        self.assertTrue(result["uiReadable"])
        self.assertFalse(result["ready"])
        self.assertEqual(result["detail"],"avd-ui-not-actionable")

    def test_private_runner_endpoint_requires_auth(self):
        without=self.client.get("/v1/android-mcp/status")
        self.assertEqual(without.status_code,401)
        wrong=self.client.get("/v1/android-mcp/status",
            headers={"Authorization":"Bearer wrong"})
        self.assertEqual(wrong.status_code,401)
        with patch.object(diagnostics,"inspect_status",
            new=AsyncMock(return_value={"ready":True,"connectionChecked":True,
                    "devices":[],"allowedRoles":[],"detail":"avd-ui-read-confirmed"})) as probe:
            ok=self.client.get("/v1/android-mcp/status",headers=self.auth)
        self.assertEqual(ok.status_code,200,ok.text)
        self.assertTrue(ok.json()["ready"])
        probe.assert_awaited_once()

if __name__=="__main__":
    unittest.main()
