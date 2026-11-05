"""Guardrails for owner-approved, one-time AVD UI taps. No live Android required."""
import asyncio
import os
import time
import unittest
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
import android_mcp_actions as action
import android_mcp_bridge as bridge
import computer_agent

RUN="12345678-1234-4123-8123-123456789abc"
SERIAL="emulator-5554"
NODE={"index":1,"role":"button","hint":"back",
      "bounds":{"x":200,"y":100,"width":40,"height":32}}
SNAPSHOT={"privacy":"geometry-only","totalNodes":1,
          "elements":[NODE],"read_only":True}

class AndroidTapTests(unittest.TestCase):
    def setUp(self):
        action._OBSERVATIONS.clear()
        self.env=patch.dict(os.environ,{"LHM_ANDROID_MCP_TAP_ENABLED":"1"},clear=False)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        action._OBSERVATIONS.clear()

    def token(self,run=RUN):
        t=action.prepare("research",run,SERIAL,SNAPSHOT)
        self.assertIsInstance(t,str)
        self.assertEqual(len(t),32)
        return t

    def args(self,token,index=1,serial=SERIAL):
        return {"serial":serial,"observation_id":token,"index":index}

    def test_operator_must_enable_write_separately(self):
        os.environ["LHM_ANDROID_MCP_TAP_ENABLED"]="0"
        self.assertFalse(action.tap_enabled())
        self.assertIsNone(action.prepare("research",RUN,SERIAL,SNAPSHOT))
        with self.assertRaises(HTTPException):
            action._claim("research",RUN,self.args("0"*32))

    def test_scope_and_one_use_not_replayable(self):
        token=self.token()
        with self.assertRaises(HTTPException):
            action._claim("quality",RUN,self.args(token))
        with self.assertRaises(HTTPException):
            action._claim("research",RUN,self.args(token))
        token=self.token()
        claimed=action._claim("research",RUN,self.args(token))
        self.assertEqual(claimed["index"],1)
        with self.assertRaises(HTTPException):
            action._claim("research",RUN,self.args(token))

    def test_expired_and_physical_denied(self):
        self.assertIsNone(action.prepare("research",RUN,"PHONE123",SNAPSHOT))
        token=self.token()
        with patch.object(action.time,"monotonic",return_value=time.monotonic()+301):
            with self.assertRaises(HTTPException):
                action._claim("research",RUN,self.args(token))
        self.assertIsNone(action.prepare("research","bad-run-id",SERIAL,SNAPSHOT))
        with self.assertRaises(HTTPException):
            action._claim("research",RUN,{"serial":"PHONE123","observation_id":token,"index":1})

    def test_success_requires_reinspection_and_owner_reconciliation(self):
        token=self.token()
        inspect=AsyncMock(side_effect=[SNAPSHOT,{**SNAPSHOT,"totalNodes":2}])
        sent=AsyncMock()
        with patch.object(action,"_send_tap",sent):
            result=asyncio.run(action.perform("research",RUN,self.args(token),inspect))
        sent.assert_awaited_once_with(SERIAL,200,100)
        self.assertEqual(result["state"],"tap_sent")
        self.assertEqual(result["ui_after"],"changed")
        self.assertTrue(result["requires_owner_reconciliation"])
        self.assertFalse(result["external_effect_verified"])
        self.assertTrue(result["confirmed_by_cli"])
        self.assertNotIn(token,str(result))
        self.assertEqual(inspect.await_count,2)
        with self.assertRaises(HTTPException):
            action._claim("research",RUN,self.args(token))

    def test_changed_ui_never_taps_and_consumes_token(self):
        token=self.token()
        changed={**SNAPSHOT,"elements":[{**NODE,"bounds":{**NODE["bounds"],"x":201}}]}
        sent=AsyncMock()
        with patch.object(action,"_send_tap",sent):
            with self.assertRaises(HTTPException):
                asyncio.run(action.perform("research",RUN,self.args(token),
                                           AsyncMock(return_value=changed)))
        sent.assert_not_awaited()
        self.assertNotIn(token,action._OBSERVATIONS)

    def test_inspection_failure_blocks_without_touch(self):
        token=self.token()
        sent=AsyncMock()
        with patch.object(action,"_send_tap",sent):
            with self.assertRaises(HTTPException):
                asyncio.run(action.perform("research",RUN,self.args(token),
                             AsyncMock(side_effect=RuntimeError("ADB offline"))))
        sent.assert_not_awaited()

    def test_after_tap_scan_fails_requires_reconciliation(self):
        token=self.token()
        sent=AsyncMock()
        with patch.object(action,"_send_tap",sent):
            data=asyncio.run(action.perform("research",RUN,self.args(token),
                AsyncMock(side_effect=[SNAPSHOT,RuntimeError("new page busy")])))
        self.assertEqual(data["ui_after"],"unavailable")
        self.assertTrue(data["requires_owner_reconciliation"])
        sent.assert_awaited_once()

    def test_emulator_only_no_raw_coordinates_or_shell(self):
        token=self.token()
        for invalid in [
            self.args(token,index=0),
            self.args(token,index=True),
            {**self.args(token),"x":10},
            self.args(token,serial="device-1234"),
        ]:
            with self.assertRaises(HTTPException):
                action._claim("research",RUN,invalid)
        self.assertIn(token,action._OBSERVATIONS)
        self.assertIn("android_mcp_tap",computer_agent.ALWAYS_APPROVAL_TOOLS)
        self.assertTrue(computer_agent._approval_required("android_mcp_tap",
                        {"mode":"trusted"}))
        self.assertEqual(bridge.TAP_TOOL,"android_mcp_tap")

    def test_simultaneous_approved_taps_same_avd_never_overlap(self):
        other_run="12345678-1234-4123-8123-123456789abd"
        first_token=self.token()
        second_token=self.token(other_run)
        inspector=AsyncMock(return_value=SNAPSHOT)

        async def scenario():
            entered=asyncio.Event()
            release=asyncio.Event()
            sent=[]
            async def blocked_adb(serial,x,y):
                sent.append((serial,x,y))
                entered.set()
                await release.wait()

            with patch.object(action,"_send_tap",side_effect=blocked_adb):
                first=asyncio.create_task(action.perform(
                    "research",RUN,self.args(first_token),inspector))
                try:
                    await asyncio.wait_for(entered.wait(),timeout=2)
                    # A tap starting in one run invalidates every prior
                    # observation on that AVD, including other run tokens.
                    self.assertNotIn(second_token,action._OBSERVATIONS)
                    # Also reject fresh snapshots from other runs until the
                    # in-flight device touch and post-inspection complete.
                    self.assertIsNone(action.prepare("research",other_run,SERIAL,SNAPSHOT))
                    with self.assertRaises(HTTPException) as busy:
                        await action.perform(
                            "research",other_run,self.args(second_token),inspector)
                    self.assertEqual(busy.exception.status_code,409)
                    self.assertNotIn(second_token,action._OBSERVATIONS)
                    self.assertEqual(sent,[(SERIAL,200,100)])
                    # Busy attempt never even re-inspected or sent a second touch.
                    self.assertEqual(inspector.await_count,1)
                finally:
                    release.set()
                receipt=await asyncio.wait_for(first,timeout=2)
            self.assertEqual(receipt["state"],"tap_sent")
            self.assertTrue(receipt["requires_owner_reconciliation"])
            self.assertEqual(inspector.await_count,2)
            self.assertNotIn(SERIAL,action._ACTIVE_TAPS)
            self.assertIsNotNone(action.prepare("research",other_run,SERIAL,SNAPSHOT))
            with self.assertRaises(HTTPException) as replay:
                action._claim("research",other_run,self.args(second_token))
            self.assertEqual(replay.exception.status_code,409)

        asyncio.run(scenario())

    def test_device_reservation_released_when_reinspection_fails(self):
        token=self.token()
        with patch.object(action,"_send_tap",new_callable=AsyncMock) as sent:
            with self.assertRaises(HTTPException):
                asyncio.run(action.perform(
                    "research",RUN,self.args(token),
                    AsyncMock(side_effect=RuntimeError("AVD disconnected"))))
        sent.assert_not_awaited()
        self.assertNotIn(SERIAL,action._ACTIVE_TAPS)
        # Failure does not make the original token reusable.
        with self.assertRaises(HTTPException):
            action._claim("research",RUN,self.args(token))

    def test_find_adb_from_standard_localappdata_sdk(self):
        with tempfile.TemporaryDirectory(prefix="android-sdk-test-") as root:
            adb=Path(root)/"Android"/"Sdk"/"platform-tools"/"adb.exe"
            adb.parent.mkdir(parents=True)
            adb.write_bytes(b"not-a-real-adb-fixture")
            with patch.dict(os.environ,{
                "ANDROID_SDK_ROOT":"",
                "ANDROID_HOME":"",
                "LOCALAPPDATA":root
            }):
                self.assertEqual(action._adb_binary(),str(adb))
            adb.unlink()
            with patch.dict(os.environ,{
                "ANDROID_SDK_ROOT":"",
                "ANDROID_HOME":"",
                "LOCALAPPDATA":root
            }):
                with self.assertRaises(HTTPException):
                    action._adb_binary()

    def test_bridge_validate_must_gate_tap(self):
        # Native Windows is mocked; actual hardware is separately required.
        with patch.object(bridge,"configured",return_value=True), \
             patch.object(bridge,"allowed_serials",return_value={SERIAL}):
            bridge.validate("research","android_mcp_tap",self.args(self.token()))
            with self.assertRaises(HTTPException):
                bridge.validate("research","android_mcp_tap",
                                self.args("0"*32,serial="PHONE123"))

if __name__=="__main__":
    unittest.main()
