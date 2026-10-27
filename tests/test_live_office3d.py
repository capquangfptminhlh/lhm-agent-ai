"""Native Three.js live office tests: real SQLite state, no synthetic activity.

No browser GPU, CDN, or paid AI calls in these checks. WebGL acceptance must
be completed separately on a real browser/device.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app
import core
import tool_hub
import office3d_api


class Live3DOfficeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="lhm_live3d_")
        self.old_db=core.DATABASE
        core.DATABASE=Path(self.tmp.name)/"office.sqlite3"
        core.setup()
        tool_hub.prepare_db()
        self.env=patch.dict(os.environ,{"LHM_ADMIN_TOKEN":"","LHM_TOOLS_WORKSPACE":"demo"})
        self.env.start()
        self.client=TestClient(app.app)

    def tearDown(self):
        self.env.stop()
        core.DATABASE=self.old_db
        self.tmp.cleanup()

    def test_scene_api_uses_actual_16_agents_and_8_departments(self):
        r=self.client.get("/api/office3d/snapshot?workspace_id=demo")
        self.assertEqual(r.status_code,200,r.text)
        d=r.json()
        self.assertEqual(d["schema_version"],1)
        self.assertEqual(d["mode"],"live_backend")
        self.assertEqual(len(d["agents"]),16)
        self.assertEqual(len(d["departments"]),8)
        self.assertEqual(d["tasks"],[])
        self.assertFalse(d["worker"]["online"])
        self.assertTrue(all(a["status"]=="idle" and a["task_id"] is None for a in d["agents"]))

    def test_task_state_and_label_come_from_persisted_data(self):
        r=self.client.post("/workspaces/demo/tasks",json={"role":"content",
                           "instruction":"Viết một bài marketing đã kiểm chứng",
                           "requires_approval":True})
        self.assertEqual(r.status_code,201,r.text)
        task_id=r.json()["id"]
        d=self.client.get("/api/office3d/snapshot?workspace_id=demo").json()
        person=next(a for a in d["agents"] if a["id"]=="content")
        self.assertEqual(person["status"],"awaiting_approval")
        self.assertEqual(person["task_id"],task_id)
        self.assertIn("marketing",person["task_title"])
        self.assertEqual(d["statuses"].get("awaiting_approval"),1)
        self.assertEqual(d["tasks"][0]["id"],task_id)
        self.assertNotIn("result",d["tasks"][0])

        approved=self.client.post("/tasks/"+task_id+"/approve")
        self.assertEqual(approved.status_code,200)
        data=self.client.get("/api/office3d/snapshot?workspace_id=demo").json()
        person=next(a for a in data["agents"] if a["id"]=="content")
        self.assertEqual(person["status"],"queued")

        with core.db() as c:
            c.execute("UPDATE tasks SET status='running',updated_at=? WHERE id=?",(core.now(),task_id))
        active=self.client.get("/api/office3d/snapshot?workspace_id=demo").json()
        self.assertEqual(next(a for a in active["agents"] if a["id"]=="content")["status"],"running")

        with core.db() as c:
            c.execute("UPDATE tasks SET status='completed',result=?,updated_at=? WHERE id=?",
                      ("Bản nháp đã kiểm duyệt",core.now(),task_id))
        finished=self.client.get("/api/office3d/snapshot?workspace_id=demo").json()
        self.assertEqual(next(a for a in finished["agents"] if a["id"]=="content")["status"],"completed")

    def test_cross_workspace_isolation(self):
        ws="another_business"
        self.assertEqual(self.client.post("/workspaces",json={
            "id":ws,"name":"Doanh nghiệp B","industry":"general","budget_usd":1.0
        }).status_code,201)
        t=self.client.post("/workspaces/demo/tasks",json={
            "role":"research","instruction":"Nghiên cứu chiến dịch bí mật A"
        })
        self.assertEqual(t.status_code,201)
        a=self.client.get("/api/office3d/snapshot?workspace_id=demo").json()
        b=self.client.get("/api/office3d/snapshot?workspace_id="+ws).json()
        self.assertEqual(len(a["tasks"]),1)
        self.assertEqual(b["tasks"],[])
        self.assertFalse(any("bí mật A" in x["task_title"] for x in b["agents"]))
        self.assertEqual(self.client.get("/api/office3d/snapshot?workspace_id=invalid").status_code,404)

    def test_worker_is_not_faked(self):
        d=self.client.get("/api/office3d/snapshot").json()
        self.assertFalse(d["worker"]["online"])
        with core.db() as c:
            c.execute("INSERT OR REPLACE INTO worker_heartbeats(id,updated_at) VALUES('default',?)",
                      (core.now(),))
        d=self.client.get("/api/office3d/snapshot").json()
        self.assertTrue(d["worker"]["online"])
        with core.db() as c:
            c.execute("UPDATE worker_heartbeats SET updated_at=? WHERE id='default'",
                      (core.now()-120,))
        self.assertFalse(self.client.get("/api/office3d/snapshot").json()["worker"]["online"])

    def test_routes_and_safe_fallback_navigation(self):
        html=self.client.get("/office3d")
        self.assertEqual(html.status_code,200)
        # Three.js r186 is vendored and served by this backend (no CDN).
        self.assertIn("/office3d/vendor/three.module.js",html.text)
        self.assertNotIn("cdn.jsdelivr.net/npm/three",html.text)
        self.assertIn("sceneFallback",html.text)
        self.assertIn("scene.js",html.text)
        self.assertIn('href="/office"',html.text)
        self.assertEqual(self.client.get("/office3d/scene.css").status_code,200)
        script=self.client.get("/office3d/scene.js")
        self.assertEqual(script.status_code,200)
        self.assertIn("EventSource",script.text)
        self.assertIn("applySnapshot",script.text)
        self.assertIn("WebGLRenderer",script.text)
        self.assertIn("text/html",html.headers.get("content-type",""))
        self.assertIn('href="/office3d"',self.client.get("/dashboard").text)
        self.assertIn('href="/office3d"',self.client.get("/office").text)

    def test_basic_auth_applies_to_scene_and_snapshot(self):
        import base64
        with patch.dict(os.environ,{"LHM_ADMIN_TOKEN":"test-3d-token"}):
            self.assertEqual(self.client.get("/office3d").status_code,401)
            self.assertEqual(self.client.get("/api/office3d/snapshot").status_code,401)
            user=base64.b64encode(b"admin:test-3d-token").decode()
            hdr={"Authorization":"Basic "+user}
            self.assertEqual(self.client.get("/office3d",headers=hdr).status_code,200)
            self.assertEqual(self.client.get("/api/office3d/snapshot",headers=hdr).status_code,200)


if __name__=="__main__":
    unittest.main()
