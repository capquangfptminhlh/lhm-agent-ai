"""P4 owner-machine blockers ported onto the security-hardened main branch.

These are offline regressions. Real Windows use still requires owner QA.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

import app
import core
import computer_agent as agent


def free_reply(body):
    return {"choices":[{"message":{"content":body}}], "usage":{}}


class TolerantActionParsingTests(unittest.TestCase):
    def test_real_free_model_wrappers_are_read_but_not_executed(self):
        for text in (
            'Phân tích nhanh rồi thực hiện.\n{"tool":"windows_list","args":{}}',
            '<think>need to inspect</think>{"tool":"windows_list","args":{}}',
            '```json\n{"tool":"windows_list","args":{}}\n```\nXong.',
            '{"tool":"windows_list","args":{},"reason":"inspect"}',
            '{"tool":"windows_list"}',
        ):
            with patch.dict(os.environ, {"LHM_DESKTOP_NATIVE":"1"}):
                self.assertEqual(agent._parse_action(text),("windows_list",{}),text)

    def test_never_accept_disallowed_tool_even_if_wrapped(self):
        for payload, status in (
            ('Ok {"tool":"format_disk","args":{}}',422),
            ('{"tool":"run_command","args":{"argv":["sh"]}}',403),
            ('Sure {"tool":"windows_click","args":[]}',502),
        ):
            with patch.dict(os.environ, {"LHM_DESKTOP_NATIVE":"1"}):
                with self.assertRaises(HTTPException) as ex:
                    agent._parse_action(payload)
            self.assertEqual(ex.exception.status_code,status)

    def test_malformed_non_text_and_overlong_reply_are_rejected(self):
        for body in (None, "", '{"args":{}}', "not JSON", "x"*48001):
            with self.assertRaises(HTTPException) as err:
                agent._parse_action(body)
            self.assertEqual(err.exception.status_code,502)

    def test_only_one_format_retry_and_no_tool_executed(self):
        run={"role":"research","workspace_id":"demo","objective":"Đọc cửa sổ",
             "model":"test/local"}
        with patch.object(agent,"model_configured",return_value=True), \
             patch.object(app,"deepseek_request",AsyncMock(side_effect=[
                 free_reply("hmm..."),free_reply('{"tool":"browser_snapshot","args":{}}')])) as send:
            self.assertEqual(asyncio.run(agent._next_action(run,[])),("browser_snapshot",{}))
        self.assertEqual(send.await_count,2)
        self.assertIn("ONLY a single JSON object",send.await_args_list[1].args[2])
        with patch.object(agent,"model_configured",return_value=True), \
             patch.object(app,"deepseek_request",AsyncMock(return_value=free_reply("still malformed"))) as send:
            with self.assertRaises(HTTPException):
                asyncio.run(agent._next_action(run,[]))
        self.assertEqual(send.await_count,2)

    def test_free_format_retry_stops_at_local_quota(self):
        run={"role":"research","workspace_id":"demo","objective":"Đọc cửa sổ",
             "model":"openrouter/free"}
        with patch.object(agent,"model_configured",return_value=True), \
             patch.object(agent,"local_free_quota",return_value={
                 "used_24h":1,"limit_24h":40,"used_1m":15,"limit_1m":15}), \
             patch.object(app,"deepseek_request",AsyncMock(return_value=free_reply("bad formatting"))) as send:
            with self.assertRaises(HTTPException) as err:
                asyncio.run(agent._next_action(run,[]))
        self.assertEqual(err.exception.status_code,429)
        self.assertEqual(send.await_count,1)


class SafePrecheckTests(unittest.TestCase):
    def test_args_limits_fail_before_runner(self):
        self.assertIn("1-1100",agent._precheck_args(
            "browser_scroll",{"direction":"down","pixels":1200}))
        self.assertIsNone(agent._precheck_args(
            "browser_scroll",{"direction":"down","pixels":800}))
        self.assertIn("hotkey",agent._precheck_args(
            "windows_hotkey",{"hwnd":11,"keys":"WIN+R"}))
        self.assertIsNone(agent._precheck_args(
            "windows_hotkey",{"hwnd":11,"keys":"ctrl+s"}))
        self.assertIn("hwnd",agent._precheck_args(
            "windows_click",{"x":1,"y":2}))
        self.assertIn("steps",agent._precheck_args(
            "windows_scroll",{"hwnd":11,"steps":20}))

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="lhm-p4-parser-")
        self.original_db=core.DATABASE
        core.DATABASE=Path(self.tmp.name)/"db.sqlite3"
        core.setup()
        agent.prepare_db()
        self.env=patch.dict(os.environ,{
            "LHM_ADMIN_TOKEN":"","LHM_COMPUTER_RUNNER_TOKEN":"test-only",
            "LHM_COMPUTER_URL":"http://computer:7781"
        })
        self.env.start()
        self.client=TestClient(app.app)

    def tearDown(self):
        self.env.stop()
        core.DATABASE=self.original_db
        self.tmp.cleanup()

    def _run(self,key):
        result=self.client.post("/workspaces/demo/computer/runs",json={
            "role":"research","objective":"Kiểm tra thao tác trong giới hạn an toàn",
            "max_steps":8,"idempotency_key":key})
        self.assertEqual(result.status_code,201,result.text)
        return result.json()["id"]

    def test_invalid_scroll_does_not_call_runner_or_kill_session(self):
        rid=self._run("p4-invalid-scroll-001")
        with patch.object(agent,"_next_action",AsyncMock(return_value=(
                 "browser_scroll",{"direction":"down","pixels":1200}))), \
             patch.object(agent,"runner_call",AsyncMock(
                 side_effect=AssertionError("No runner allowed"))) as runner:
            result=self.client.post("/computer/runs/"+rid+"/tick")
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()["status"],"queued")
        self.assertEqual(result.json()["step_status"],"failed")
        self.assertIs(result.json()["action_executed"],False)
        runner.assert_not_awaited()
        view=self.client.get("/computer/runs/"+rid).json()
        self.assertEqual(view["steps"][0]["status"],"failed")

    def test_owner_rejected_action_is_never_proposed_twice(self):
        rid=self._run("p4-refused-click-002")
        request=("browser_click",{"selector":"#send"})
        with patch.object(agent,"_next_action",AsyncMock(return_value=request)), \
             patch.object(agent,"runner_call",AsyncMock(
                 side_effect=AssertionError("No runner allowed"))) as runner:
            pending=self.client.post("/computer/runs/"+rid+"/tick").json()
            self.assertEqual(pending["status"],"awaiting_approval")
            rejected=self.client.post(
                "/computer/runs/"+rid+"/steps/"+pending["step_id"]+"/reject")
            self.assertEqual(rejected.status_code,200,rejected.text)
            blocked=self.client.post("/computer/runs/"+rid+"/tick").json()
            self.assertEqual(blocked["status"],"queued")
            self.assertIs(blocked["action_executed"],False)
            stopped=self.client.post("/computer/runs/"+rid+"/tick").json()
            self.assertEqual(stopped["status"],"needs_review")
            runner.assert_not_awaited()
        steps=self.client.get("/computer/runs/"+rid).json()["steps"]
        self.assertEqual([x["status"] for x in steps],["rejected","failed","failed"])
        self.assertEqual(self.client.post("/computer/runs/"+rid+"/tick").status_code,409)


if __name__=="__main__":
    unittest.main()
