"""Offline E2E contract: one authorized mission queues 16 fixed-role jobs atomically.

Worker runs real queue+SQLite and mocked model. No external provider/API calls.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

import app
import core
import agent_work_orders as work
import worker


class SpecialistFleetTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="lhm_fleet_")
        self.addCleanup(self.tmp.cleanup)
        previous=core.DATABASE
        core.DATABASE=Path(self.tmp.name)/"agent.sqlite3"
        self.addCleanup(setattr,core,"DATABASE",previous)
        core.setup()
        work.prepare_db()
        env=patch.dict(os.environ,{
            "LHM_ADMIN_TOKEN":"",
            "LHM_OPENROUTER_FREE_ONLY":"1",
            "OPENROUTER_API_KEY":"offline-test-key",
            "LHM_TOOLS_WORKSPACE":"demo",
        })
        env.start()
        self.addCleanup(env.stop)
        self.client=TestClient(app.app)

    def mission(self,key="fleet-2026-10-28-one",mission="Xây dựng kế hoạch vận hành và bán hàng trong tháng tới"):
        return {"mission":mission,"context":"Bài toán minh họa, không có tài khoản thật",
                "idempotency_key":key}

    def create(self,**kw):
        data=self.mission(**kw)
        resp=self.client.post("/workspaces/demo/agent-fleets",json=data)
        self.assertEqual(resp.status_code,201,resp.text)
        return resp.json()

    def test_atomic_16_unique_fixed_roles_and_persisted_task_records(self):
        f=self.create()
        self.assertEqual(f["role_count"],16)
        self.assertEqual(f["queued"],16)
        self.assertEqual(f["drafts_ready"],0)
        self.assertEqual(f["status"],"queued")
        self.assertFalse(f["external_actions_performed"])
        self.assertFalse(f["external_verification"])
        self.assertEqual({x["role"] for x in f["orders"]},set(core.ROLES))
        self.assertEqual(len({x["task_id"] for x in f["orders"]}),16)
        with core.db() as c:
            for row in f["orders"]:
                task=c.execute("SELECT role,instruction,status FROM tasks WHERE id=?",(row["task_id"],)).fetchone()
                self.assertEqual(task["role"],row["role"])
                self.assertEqual(task["status"],"queued")
                self.assertIn("NGHỀ CỐ ĐỊNH:",task["instruction"])
                self.assertIn("TIÊU CHÍ BÀN GIAO",task["instruction"])
            self.assertEqual(c.execute("SELECT COUNT(*) FROM tool_jobs").fetchone()[0],0)
        self.assertEqual(self.client.get("/workspaces/demo/agent-fleets/"+f["id"]).json()["role_count"],16)

    def test_idempotent_refresh_and_conflicting_payload(self):
        a=self.create()
        b=self.create()
        self.assertEqual(a["id"],b["id"])
        self.assertTrue(b["deduplicated"])
        self.assertEqual(self.client.get("/workspaces/demo/agent-fleets").json()["fleets"][0]["id"],a["id"])
        resp=self.client.post("/workspaces/demo/agent-fleets",json=self.mission(mission="Mục tiêu khác có độ dài hợp lệ"))
        self.assertEqual(resp.status_code,409)
        with core.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM agent_fleets").fetchone()[0],1)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM agent_work_orders").fetchone()[0],16)

    def test_invalid_input_fails_without_side_effects(self):
        for body in [
            {"mission":"short","idempotency_key":"valid-key-12345"},
            {"mission":"Mục tiêu đủ dài nhưng khóa chứa dấu cách","idempotency_key":"bad key 123"},
            {"mission":"Mục tiêu đủ dài","idempotency_key":"valid-key-12345","context":"a"*3001},
        ]:
            self.assertEqual(self.client.post("/workspaces/demo/agent-fleets",json=body).status_code,422)
        with core.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM agent_fleets").fetchone()[0],0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM agent_work_orders").fetchone()[0],0)

    def test_transaction_rolls_back_entire_fleet_on_one_role_error(self):
        original=work._create_order_locked
        def broken(c,ws,role,inp,model):
            if role=="strategy":
                raise HTTPException(503,"synthetic one-role failure")
            return original(c,ws,role,inp,model)
        with patch.object(work,"_create_order_locked",side_effect=broken):
            res=self.client.post("/workspaces/demo/agent-fleets",json=self.mission())
        self.assertEqual(res.status_code,503,res.text)
        with core.db() as c:
            for table in ("agent_fleets","agent_fleet_orders","agent_work_orders","tasks"):
                self.assertEqual(c.execute("SELECT COUNT(*) FROM "+table).fetchone()[0],0,table)

    def test_cross_workspace_isolation(self):
        f=self.create()
        created=self.client.post("/workspaces",json={
            "id":"other-fleet-ws","name":"Another Space","industry":"ecommerce","budget_usd":1.0})
        self.assertEqual(created.status_code,201,created.text)
        self.assertEqual(self.client.get("/workspaces/other-fleet-ws/agent-fleets/"+f["id"]).status_code,404)
        self.assertEqual(self.client.get("/workspaces/other-fleet-ws/agent-fleets").json()["fleets"],[])
        self.assertEqual(self.client.post("/workspaces/missing-ws/agent-fleets",json=self.mission()).status_code,404)

    def test_worker_produces_draft_and_does_not_fabricate_external_completion(self):
        f=self.create()
        director=next(x for x in f["orders"] if x["role"]=="director")
        with core.db() as c:
            task=c.execute("SELECT id,role,workspace_id,model FROM tasks WHERE id=?",
                           (director["task_id"],)).fetchone()
        reply={"choices":[{"message":{"content":"Phân tích nhu cầu, chưa thực hiện ngoài hệ thống."}}],
               "usage":{"prompt_tokens":12,"completion_tokens":18,"cost":0}}
        with patch.object(app,"deepseek_request",new=AsyncMock(return_value=reply)):
            done=asyncio.run(worker.dispatch_agent_batch([task]))
        self.assertEqual(done,1)
        got=self.client.get("/workspaces/demo/agent-fleets/"+f["id"]).json()
        self.assertEqual(got["drafts_ready"],1)
        self.assertEqual(got["queued"],15)
        self.assertFalse(got["external_verification"])
        order=self.client.get("/workspaces/demo/agent-work-orders/"+director["order_id"]).json()
        self.assertIn("chưa thực hiện",order["draft"])
        self.assertFalse(order["external_action_completed"])

    def test_all_drafts_ready_is_not_a_provider_receipt(self):
        f=self.create()
        with core.db() as c:
            c.execute("UPDATE tasks SET status='completed',result='Bản nháp giả lập' WHERE id IN "
                      "(SELECT o.task_id FROM agent_fleet_orders f JOIN agent_work_orders o "
                      "ON o.id=f.work_order_id WHERE f.fleet_id=?)",(f["id"],))
        out=self.client.get("/workspaces/demo/agent-fleets/"+f["id"]).json()
        self.assertEqual(out["status"],"drafts_ready_unverified")
        self.assertEqual(out["drafts_ready"],16)
        self.assertFalse(out["external_verification"])

    def test_unified_work_page_ui_syntax_and_repo_diagnosis_route(self):
        html=self.client.get("/agent-work").text
        self.assertIn('id="fleetStart"',html)
        self.assertIn('id="fleetRefresh"',html)
        self.assertIn('Xem báo cáo Repo',html)
        self.assertNotIn('selectedOrder=r.id',html)
        node=shutil.which("node")
        if not node:self.skipTest("Node missing")
        block=re.search(r"<script>(.*?)</script>",html,re.S)
        self.assertIsNotNone(block)
        with tempfile.NamedTemporaryFile(mode="w",suffix=".js",encoding="utf-8") as f:
            f.write(block.group(1));f.flush()
            result=subprocess.run([node,"--check",f.name],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)


if __name__=="__main__":
    unittest.main()
