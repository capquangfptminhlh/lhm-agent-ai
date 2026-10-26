"""Offline checks for local deployment, browser login and durable tool execution."""
import asyncio
import base64
import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from fastapi.testclient import TestClient

import app
import core
import tool_hub
import worker


class ProductionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original_db = core.DATABASE
        core.DATABASE = Path(self.temp.name) / "test.sqlite3"
        core.setup()
        tool_hub.prepare_db()
        self.client = TestClient(app.app)
        self.env = patch.dict(os.environ, {"LHM_ADMIN_TOKEN": "", "LHM_TOOLS_WORKSPACE": "demo"})
        self.env.start()
        with core.db() as c:
            c.execute("DELETE FROM tool_jobs WHERE workspace_id='demo'")

    def tearDown(self):
        self.env.stop()
        core.DATABASE = self.original_db
        self.temp.cleanup()

    def test_browser_basic_auth_and_csrf_guard(self):
        with patch.dict(os.environ, {"LHM_ADMIN_TOKEN": "a-long-admin-token"}):
            self.assertEqual(self.client.get("/office").status_code, 401)
            token = base64.b64encode(b"admin:a-long-admin-token").decode()
            headers = {"Authorization": "Basic " + token}
            self.assertEqual(self.client.get("/office", headers=headers).status_code, 200)
            self.assertEqual(self.client.post("/workspaces/demo/tasks", headers={**headers, "Origin": "https://other.example"},
                      json={"instruction": "Viết bài quảng cáo cho dự án"}).status_code, 403)
            good = self.client.post("/workspaces/demo/tasks", headers=headers,
                      json={"instruction": "Viết bài quảng cáo cho dự án"})
            self.assertEqual(good.status_code, 201, good.text)
            bearer = self.client.get('/catalog',headers={'Authorization':'Bearer a-long-admin-token'})
            self.assertEqual(bearer.status_code,200)

    def test_from_agent_requires_actual_completed_task(self):
        base = {"source_task_id": "not-a-real-task", "action": "wordpress.create_post",
                "idempotency_key": "from-agent-01", "title": "Căn hộ Bình Thạnh"}
        self.assertEqual(self.client.post('/workspaces/demo/tool-jobs/from-agent',json=base).status_code,422)
        task=self.client.post('/workspaces/demo/tasks',json={'role':'content','instruction':'Viết bài cho căn hộ Bình Thạnh'}).json()['id']
        base['source_task_id']=task
        self.assertEqual(self.client.post('/workspaces/demo/tool-jobs/from-agent',json=base).status_code,422)
        with core.db() as c:
            c.execute("UPDATE tasks SET status='completed',result=? WHERE id=?", ('Nội dung thật do Agent tạo',task))
            import publication_gate as pg
            c.execute("""INSERT INTO qa_reviews(qa_task_id,workspace_id,source_task_id,source_digest,role,verdict)
                         VALUES(?,?,?,?,?,?)""",
                      (core.new_id(),'demo',task,pg.digest('Nội dung thật do Agent tạo'),'quality','pass'))
        x=self.client.post('/workspaces/demo/tool-jobs/from-agent',json=base)
        self.assertEqual(x.status_code,201,x.text)
        self.assertEqual(x.json()['status'],'awaiting_approval')
        self.assertEqual(x.json()['payload']['content'],'Nội dung thật do Agent tạo')
        y=self.client.post('/workspaces/demo/tool-jobs/from-agent',json=base)
        self.assertTrue(y.json()['deduplicated'])
        self.assertEqual(x.json()['id'],y.json()['id'])

    def test_approved_jobs_consumed_once_and_stale_reconciled(self):
        x=self.client.post('/workspaces/demo/tool-jobs',json={
            'action':'activepieces.trigger', 'idempotency_key':'flow-once-001',
            'payload':{'data':{'event':'hello'}}}).json()
        ident=x['id']
        self.assertEqual(self.client.post(f'/tool-jobs/{ident}/approve').status_code,200)
        async def fake_dispatch(action,payload):
            return {'provider':'activepieces','confirmation':'workflow_triggered_unverified'}
        with patch.dict(os.environ,{'LHM_ACTIVEPIECES_WEBHOOK_URL':'https://flow.example/webhook'}):
            with patch.object(tool_hub,'dispatch_tool',side_effect=fake_dispatch) as call:
                self.assertTrue(asyncio.run(worker.process_one_approved_tool()))
                self.assertFalse(asyncio.run(worker.process_one_approved_tool()))
                self.assertEqual(call.call_count,1)
        row=self.client.get('/workspaces/demo/tool-jobs').json()['jobs'][0]
        self.assertEqual(row['status'],'accepted_unverified')
        with core.db() as c:
            c.execute("UPDATE tool_jobs SET status='executing',updated_at=? WHERE id=?", (core.now()-601,ident))
        asyncio.run(worker.recover_stuck())
        row=self.client.get('/workspaces/demo/tool-jobs').json()['jobs'][0]
        self.assertEqual(row['status'],'needs_reconciliation')
        self.assertEqual(self.client.post(f'/tool-jobs/{ident}/execute').status_code,409)

    def test_wordpress_readback_only_marks_verified_if_live(self):
        payload={'title':'Bài test','content':'Nội dung','status':'publish'}
        r=self.client.post('/workspaces/demo/tool-jobs',json={
            'action':'wordpress.create_post','idempotency_key':'verify-post-001','payload':payload})
        job=r.json()['id']
        self.client.post(f'/tool-jobs/{job}/approve')
        with core.db() as c:
            c.execute("UPDATE tool_jobs SET status='accepted_unverified',result_json=? WHERE id=?",
                      ('{"provider":"wordpress","post_id":42}',job))
        async def verified(receipt):
            self.assertEqual(receipt['post_id'],42)
            return {'verified':True,'provider_status':'publish','public_url':'https://site.example/post','public_http_status':200}
        with patch.object(tool_hub,'_verify_wordpress_public',side_effect=verified):
            r=self.client.post(f'/tool-jobs/{job}/verify')
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['status'],'verified_published')
        self.assertEqual(self.client.post(f'/tool-jobs/{job}/execute').status_code,409)

    def test_wordpress_readback_not_public_keeps_unverified(self):
        r=self.client.post('/workspaces/demo/tool-jobs',json={
            'action':'wordpress.create_post','idempotency_key':'verify-post-002',
            'payload':{'title':'Draft','content':'Nội dung','status':'draft'}})
        job=r.json()['id']
        with core.db() as c:
            c.execute("UPDATE tool_jobs SET status='accepted_unverified',result_json=? WHERE id=?",
                      ('{"provider":"wordpress","post_id":43}',job))
        async def not_public(receipt):
            return {'verified':False,'provider_status':'draft','reason':'Not published'}
        with patch.object(tool_hub,'_verify_wordpress_public',side_effect=not_public):
            r=self.client.post(f'/tool-jobs/{job}/verify')
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['status'],'accepted_unverified')

    def test_sensitive_provider_fields_redacted(self):
        result=tool_hub._sanitize_response({'client_secret':'test','token':'abc',
                     'nested':{'authorization':'Bearer xyz','message':'ok'}})
        self.assertNotIn('client_secret',result)
        self.assertNotIn('token',result)
        self.assertEqual(result['nested'],{'message':'ok'})


if __name__=='__main__':
    unittest.main()
