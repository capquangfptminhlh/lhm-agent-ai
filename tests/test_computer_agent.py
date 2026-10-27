"""Codex-like Computer: offline loop, actual sandbox files/terminal, approvals.

Never invokes a live LLM or a public website. Runner uses temporary dirs.
Browser UI is tested separately using Playwright on CI.
"""
from __future__ import annotations
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import app
import core
import computer_agent as agent
import computer_executor as executor


class ComputerAgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="lhm-computer-tests-")
        self.original=core.DATABASE
        core.DATABASE=Path(self.tmp.name)/"db.sqlite3"
        core.setup()
        agent.prepare_db()
        self.env=patch.dict(os.environ,{
            "LHM_ADMIN_TOKEN":"",
            "LHM_COMPUTER_RUNNER_TOKEN":"test-only-runner-secret",
            "LHM_COMPUTER_URL":"http://computer:7781",
            "LHM_COMPUTER_AUTO_WRITE":"0",
            "LHM_COMPUTER_ALLOWED_HOSTS":"example.com",
        })
        self.env.start()
        self.runner_home=patch.object(executor,"ROOT",Path(self.tmp.name)/"agent-data")
        self.runner_home.start()
        self.client=TestClient(app.app)
        self.sandbox=TestClient(executor.runner)
        self.auth={"Authorization":"Bearer test-only-runner-secret"}

    def tearDown(self):
        self.runner_home.stop()
        self.env.stop()
        core.DATABASE=self.original
        self.tmp.cleanup()

    def create(self,role="research",objective="Nghiên cứu dữ liệu công khai và ghi báo cáo",max_steps=8,key="computer-test-case-001"):
        r=self.client.post("/workspaces/demo/computer/runs",json={
            "role":role,"objective":objective,"max_steps":max_steps,"idempotency_key":key
        })
        self.assertEqual(r.status_code,201,r.text)
        return r.json()

    def post_action(self,tool,args,ws="demo",role="research"):
        return self.sandbox.post("/v1/tool",headers=self.auth,
                                json={"workspace_id":ws,"role":role,"tool":tool,"args":args})

    def test_create_persists_and_deduplicates(self):
        r=self.create()
        self.assertEqual(r["status"],"queued")
        self.assertEqual(r["step_count"],0)
        repeat=self.create()
        self.assertTrue(repeat["deduplicated"])
        self.assertEqual(repeat["id"],r["id"])
        conflict=self.client.post("/workspaces/demo/computer/runs",json={
            "role":"sales","objective":"Gửi báo cáo khách hàng cho quản lý",
            "idempotency_key":"computer-test-case-001"})
        self.assertEqual(conflict.status_code,409)
        self.assertEqual(len(self.client.get("/workspaces/demo/computer/runs").json()["runs"]),1)

    def test_llm_observation_action_approval_execution_and_finish(self):
        r=self.create()
        url="/computer/runs/"+r["id"]+"/tick"
        with patch.object(agent,"_next_action",AsyncMock(return_value=("list_files",{"path":"."}))):
            with patch.object(agent,"runner_call",AsyncMock(return_value={"entries":[]})) as tool:
                response=self.client.post(url)
                self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(response.json().get("last_tool"),"list_files",response.text)
                tool.assert_awaited_once()
        with patch.object(agent,"_next_action",AsyncMock(return_value=(
            "run_command",{"argv":["python","-c","print('hello')"]}))):
            with patch.object(agent,"runner_call",AsyncMock()) as tool:
                waiting=self.client.post(url).json()
                self.assertEqual(waiting["status"],"awaiting_approval")
                tool.assert_not_awaited()
        data=self.client.get("/computer/runs/"+r["id"]).json()
        step=data["steps"][-1]
        self.assertEqual(step["tool"],"run_command")
        self.assertEqual(step["status"],"awaiting_approval")
        with patch.object(agent,"runner_call",AsyncMock(return_value={
            "stdout":"hello\n","stderr":"","exit_code":0,"timed_out":False})) as tool:
            granted=self.client.post("/computer/runs/"+r["id"]+"/steps/"+step["id"]+"/approve")
            self.assertEqual(granted.status_code,200)
            self.assertEqual(granted.json()["status"],"queued")
            with patch.object(agent,"_next_action",side_effect=AssertionError("Approved step must run, not prompt model")):
                ran=self.client.post(url)
            self.assertEqual(ran.status_code,200,ran.text)
            tool.assert_awaited_once()
        with patch.object(agent,"_next_action",AsyncMock(return_value=(
            "finish",{"summary":"Đã chạy lệnh kiểm chứng",
                      "evidence":"Step #2 exit_code 0"}))):
            final=self.client.post(url)
        self.assertEqual(final.status_code,200,final.text)
        self.assertEqual(final.json()["status"],"completed_unverified")
        saved=self.client.get("/computer/runs/"+r["id"]).json()
        self.assertEqual(len(saved["steps"]),2)
        self.assertEqual(saved["steps"][1]["result"]["exit_code"],0)
        self.assertEqual(saved["status"],"completed_unverified")
        self.assertEqual(self.client.post(url).status_code,409)

    def test_nonzero_command_exit_is_a_failed_observation(self):
        r=self.create(key="computer-nonzero-001")
        with patch.object(agent,"_next_action",AsyncMock(return_value=(
            "run_command",{"argv":["python","-c","raise SystemExit(2)"]}))):
            wait=self.client.post("/computer/runs/"+r["id"]+"/tick").json()
        self.assertEqual(wait["status"],"awaiting_approval")
        self.client.post("/computer/runs/"+r["id"]+"/steps/"+wait["step_id"]+"/approve")
        with patch.object(agent,"runner_call",AsyncMock(return_value={
            "exit_code":2,"timed_out":False,"stdout":"","stderr":"failed test"})):
            result=self.client.post("/computer/runs/"+r["id"]+"/tick")
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()["step_status"],"failed")
        saved=self.client.get("/computer/runs/"+r["id"]).json()
        self.assertEqual(saved["steps"][0]["status"],"failed")
        self.assertEqual(saved["status"],"queued")

    def test_rejection_skips_command_and_prompts_for_alternative(self):
        r=self.create(key="computer-rejection-001")
        with patch.object(agent,"_next_action",AsyncMock(return_value=(
            "browser_click",{"selector":"#confirm"}))):
            with patch.object(agent,"runner_call",AsyncMock()) as tool:
                data=self.client.post("/computer/runs/"+r["id"]+"/tick").json()
                self.assertEqual(data["status"],"awaiting_approval")
                tool.assert_not_awaited()
        sid=data["step_id"]
        rejected=self.client.post("/computer/runs/"+r["id"]+"/steps/"+sid+"/reject")
        self.assertEqual(rejected.status_code,200)
        self.assertEqual(rejected.json()["steps"][0]["status"],"rejected")
        self.assertEqual(self.client.post("/computer/runs/"+r["id"]+"/steps/"+sid+"/approve").status_code,409)

    def test_finish_without_evidence_cannot_fabricate_success(self):
        r=self.create(key="computer-evidence-001")
        with patch.object(agent,"_next_action",AsyncMock(return_value=(
            "finish",{"summary":"Xong rồi", "evidence":"Tự khai"}))):
            response=self.client.post("/computer/runs/"+r["id"]+"/tick")
        self.assertEqual(response.json()["status"],"needs_review")
        self.assertNotIn("completed",str(self.client.get("/computer/runs/"+r["id"]).json()["status"]))

    def test_step_limit_stops_without_claiming_success(self):
        r=self.create(max_steps=1,key="computer-limit-001")
        with patch.object(agent,"_next_action",AsyncMock(return_value=("list_files",{"path":"."}))):
            with patch.object(agent,"runner_call",AsyncMock(return_value={"entries":[]})):
                self.assertEqual(self.client.post("/computer/runs/"+r["id"]+"/tick").status_code,200)
        second=self.client.post("/computer/runs/"+r["id"]+"/tick")
        self.assertEqual(second.json().get("status"),"needs_review",second.text)

    def test_sandbox_auth_and_file_jail(self):
        payload={"workspace_id":"demo","role":"research","tool":"list_files","args":{"path":"."}}
        self.assertEqual(self.sandbox.post("/v1/tool",json=payload).status_code,401)
        self.assertEqual(self.sandbox.get("/health",headers=self.auth).status_code,200)
        denied=self.post_action("read_file",{"path":"../../sales/files/private.txt"})
        self.assertEqual(denied.status_code,403)
        write=self.post_action("write_file",{"path":"reports/summary.md","content":"Dữ liệu mẫu"})
        self.assertEqual(write.status_code,200,write.text)
        view=self.post_action("read_file",{"path":"reports/summary.md"})
        self.assertEqual(view.json()["content"],"Dữ liệu mẫu")
        self.assertEqual(self.post_action("list_files",{"path":"reports"}).json()["entries"][0]["name"],"summary.md")
        other=self.post_action("list_files",{"path":"."},role="sales")
        self.assertEqual(other.json()["entries"],[])
        self.assertEqual(self.post_action("write_file",{"path":"../x","content":"x"}).status_code,403)

    def test_exact_file_edit_requires_single_match(self):
        self.post_action("write_file",{"path":"code.py","content":"a=1\nb=2\n"})
        edit=self.post_action("edit_file",{"path":"code.py","old":"b=2","new":"b=3"})
        self.assertEqual(edit.status_code,200,edit.text)
        self.assertEqual(self.post_action("read_file",{"path":"code.py"}).json()["content"],"a=1\nb=3\n")
        collision=self.post_action("edit_file",{"path":"code.py","old":"x=99","new":"bad"})
        self.assertEqual(collision.status_code,409)

    def test_terminal_is_real_argv_no_shell_and_env_scrubbed(self):
        self.assertEqual(self.post_action("run_command",{"argv":["bash","-c","echo blocked"]}).status_code,403)
        self.assertEqual(self.post_action("run_command",{"argv":["/usr/bin/python3","-c","print(1)"]}).status_code,403)
        result=self.post_action("run_command",{"argv":["python","-c",
            "import os; print('hello-codex-sandbox'); print(os.getenv('LHM_COMPUTER_RUNNER_TOKEN','hidden'))"]})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()["exit_code"],0)
        self.assertIn("hello-codex-sandbox",result.json()["stdout"])
        self.assertNotIn("test-only-runner-secret",str(result.json()))

    def test_operator_browser_domain_allowlist(self):
        self.assertTrue(executor._allowed_domain("https://example.com/page"))
        for host in ["http://example.com","https://evil.example.com","https://localhost/",
                     "https://127.0.0.1/","https://example.com.evil.test/","https://example.com:8080/"]:
            self.assertFalse(executor._allowed_domain(host),host)

    def test_runner_url_not_arbitrary_ssrf(self):
        for url in ["http://169.254.169.254/","https://attacker.example.net",
                    "http://computer:7781/admin","http://evil:7781"]:
            with patch.dict(os.environ,{"LHM_COMPUTER_URL":url}):
                with self.assertRaises(Exception):
                    agent._runner_base()
        self.assertEqual(agent._runner_base(),"http://computer:7781")

    def test_stale_worker_requires_review_not_automatic_retry(self):
        r=self.create(key="computer-crash-001")
        with core.db() as c:
            c.execute("UPDATE computer_runs SET status='processing',updated_at=? WHERE id=?",
                      (core.now()-600,r["id"]))
        agent.recover_computer_runs()
        saved=self.client.get("/computer/runs/"+r["id"]).json()
        self.assertEqual(saved["status"],"needs_review")
        self.assertEqual(self.client.post("/computer/runs/"+r["id"]+"/tick").status_code,409)

    def test_workbench_javascript_syntax(self):
        import re
        import shutil
        import subprocess
        node=shutil.which("node")
        if not node:self.skipTest("Node missing")
        html=self.client.get("/computer").text
        self.assertIn("Computer Console",html)
        self.assertIn("Chụp màn hình",html)
        match=re.search(r"<script>(.*?)</script>",html,re.S)
        self.assertIsNotNone(match)
        with tempfile.NamedTemporaryFile(mode="w",suffix=".js",encoding="utf-8") as handle:
            handle.write(match.group(1));handle.flush()
            chk=subprocess.run([node,"--check",handle.name],capture_output=True,text=True)
        self.assertEqual(chk.returncode,0,chk.stderr)


if __name__=="__main__":
    unittest.main()
