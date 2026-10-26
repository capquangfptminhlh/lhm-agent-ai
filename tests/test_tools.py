"""Offline tool-hub tests: no outside writes, all provider calls mocked."""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if not os.environ.get('LHM_DB_PATH'):
    os.environ['LHM_DB_PATH'] = str(Path(tempfile.mkdtemp()) / 'toolhub.sqlite3')
os.environ['LHM_ADMIN_TOKEN'] = ''
from fastapi.testclient import TestClient
import app
import core
import tool_hub as hub
import httpx

class ToolHubTests(unittest.TestCase):
    def setUp(self):
        self.c = TestClient(app.app)
        self.ws = 'demo'
        with core.db() as c:
            c.execute('DELETE FROM tool_jobs WHERE workspace_id=?',(self.ws,))

    def create(self, action='postiz.create_post', p=None, idem='tooljob-01'):
        if p is None:
            p={'mode':'draft','channel_ids':['ch-facebook'],'content':'Bài viết thử nghiệm', 'media':[]}
        return self.c.post('/workspaces/demo/tool-jobs',json={'action':action,'payload':p,'idempotency_key':idem})

    def test_matrix_exhaustive_and_html_loaded(self):
        r=self.c.get('/tools/catalog')
        self.assertEqual(r.status_code,200)
        d=r.json()
        self.assertEqual(len(d['roles']),16)
        self.assertEqual(len(d['jobs']),66)
        self.assertEqual(len(d['repositories']),19)
        self.assertIn('html',self.c.get('/tool-hub').headers.get('content-type',''))
        self.assertIn('Tool Hub',self.c.get('/tool-hub').text)

    def test_approval_gate_and_idempotency(self):
        r=self.create()
        self.assertEqual(r.status_code,201,r.text)
        data=r.json(); ident=data['id']
        self.assertEqual(data['status'],'awaiting_approval')
        self.assertEqual(self.c.post(f'/tool-jobs/{ident}/execute').status_code,409)
        dup=self.create()
        self.assertTrue(dup.json()['deduplicated'])
        self.assertEqual(dup.json()['id'],ident)
        collision=self.create(p={'mode':'draft','channel_ids':['ch-facebook'],'content':'KHÁC'})
        self.assertEqual(collision.status_code,409)
        self.assertEqual(self.c.post(f'/tool-jobs/{ident}/approve').status_code,200)
        self.assertEqual(self.c.post(f'/tool-jobs/{ident}/approve').status_code,409)

    def test_pilot_tenant_isolation(self):
        ws='another'+core.new_id().replace('-','')[:10]
        self.c.post('/workspaces',json={'id':ws,'name':'Company example','industry':'general','budget_usd':5})
        self.assertEqual(self.c.get(f'/workspaces/{ws}/tool-jobs').status_code,403)
        self.assertEqual(self.c.post(f'/workspaces/{ws}/tool-jobs',json={
            'action':'postiz.create_post','payload':{'mode':'draft','content':'hello','channel_ids':['ch-facebook']},'idempotency_key':'tenant-job'}).status_code,403)

    def test_invalid_schedule_is_rejected(self):
        p={'mode':'schedule','channel_ids':['ch-facebook'],'content':'Sample','scheduled_at':'2020-01-01T00:00:00Z'}
        self.assertEqual(self.create(p=p).status_code,422)

    def test_postiz_multichannel_accepted_but_not_published(self):
        calls=[]
        async def mock_postiz(method,endpoint,data=None):
            calls.append((method,endpoint,data))
            if method=='GET':
                return [{'id':'ch-facebook','identifier':'facebook','name':'Facebook Page','disabled':False},
                        {'id':'ch-linkedin','identifier':'linkedin','name':'LinkedIn','disabled':False}]
            return [{'postId':'post-11','integration':'ch-facebook'}, {'postId':'post-12','integration':'ch-linkedin'}]
        p={'mode':'now','channel_ids':['ch-facebook','ch-linkedin'],'content':'BĐS đáng chú ý, hãy kiểm chứng thông tin trước khi đăng','media':[]}
        ident=self.create(p=p).json()['id']
        with patch.object(hub,'_postiz_request',side_effect=mock_postiz):
            self.c.post(f'/tool-jobs/{ident}/approve')
            result=self.c.post(f'/tool-jobs/{ident}/execute')
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['status'],'accepted_unverified')
        self.assertEqual(result.json()['receipt']['confirmation'],'submitted_unverified')
        self.assertEqual(calls[0][:2],('GET','/integrations'))
        self.assertEqual(calls[1][:2],('POST','/posts'))
        data=calls[1][2]
        self.assertEqual(data['type'],'now')
        self.assertEqual({x['settings']['__type'] for x in data['posts']},{'facebook','linkedin'})
        # Exactly-once local: the exact same action cannot POST again.
        self.assertEqual(self.c.post(f'/tool-jobs/{ident}/execute').status_code,409)
        self.assertEqual(len(calls),2)

    def test_failed_media_validation_never_publishes(self):
        called=[]
        async def mock_postiz(method,endpoint,data=None):
            called.append(method)
            return [{'id':'ch-ig','identifier':'instagram','disabled':False}]
        p={'mode':'now','channel_ids':['ch-ig'],'content':'caption'}
        ident=self.create(p=p).json()['id']
        self.c.post(f'/tool-jobs/{ident}/approve')
        with patch.object(hub,'_postiz_request',side_effect=mock_postiz):
            resp=self.c.post(f'/tool-jobs/{ident}/execute')
        self.assertEqual(resp.status_code,502)
        self.assertEqual(called,['GET'])
        self.assertEqual(self.c.get('/workspaces/demo/tool-jobs').json()['jobs'][0]['status'],'failed_validation')

    def test_provider_timeout_is_ambiguous_no_retry(self):
        async def fail(*args,**kwargs):
            raise httpx.ReadTimeout('simulated')
        ident=self.create().json()['id']
        self.c.post(f'/tool-jobs/{ident}/approve')
        with patch.object(hub,'_postiz_request',side_effect=fail):
            resp=self.c.post(f'/tool-jobs/{ident}/execute')
        self.assertEqual(resp.status_code,502)
        self.assertEqual(self.c.get('/workspaces/demo/tool-jobs').json()['jobs'][0]['status'],'needs_reconciliation')
        self.assertEqual(self.c.post(f'/tool-jobs/{ident}/execute').status_code,409)

    def test_postiz_live_channel_listing(self):
        async def fake(method,endpoint,data=None):
            return [{'id':'m1','name':'A','identifier':'facebook','disabled':False},
                    {'id':'m2','name':'B','identifier':'instagram','disabled':True}]
        with patch.object(hub,'_postiz_request',side_effect=fake):
            r=self.c.get('/tools/postiz/channels')
        self.assertEqual(r.status_code,200,r.text)
        self.assertTrue(r.json()['connection_verified'])
        self.assertEqual(r.json()['count'],2)

    def test_wordpress_adapter_stores_provider_receipt_only(self):
        class Client:
            def __init__(self,**kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self,*args): return False
            async def post(self,url,json=None):
                self.url=url; self.body=json
                class R:
                    status_code=201
                    def raise_for_status(self): return None
                    def json(self): return {"id":118,"status":"publish","link":"https://site.example/post"}
                return R()
        payload={'title':'Bài nhà ở','content':'Bài thử nghiệm','status':'publish'}
        ident=self.create(action='wordpress.create_post',p=payload,idem='wp-job-01').json()['id']
        self.c.post(f'/tool-jobs/{ident}/approve')
        with patch.dict(os.environ, {'LHM_WP_URL':'https://site.example','LHM_WP_USER':'test','LHM_WP_APP_PASSWORD':'fake'}):
            with patch.object(hub.httpx,'AsyncClient',Client):
                r=self.c.post(f'/tool-jobs/{ident}/execute')
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['receipt']['post_id'],118)
        self.assertEqual(r.json()['receipt']['confirmation'],'provider_record_created')

    def test_activepieces_workflow_adapter(self):
        class Client:
            def __init__(self,**kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self,*args): return False
            async def post(self,url,json=None):
                assert url=='https://flow.example/webhook/abc'
                assert json['data']['lead_id']=='lead123'
                class R:
                    status_code=200
                    def raise_for_status(self): return None
                return R()
        ident=self.create(action='activepieces.trigger',p={'data':{'lead_id':'lead123'}},idem='activepieces-job-01').json()['id']
        self.c.post(f'/tool-jobs/{ident}/approve')
        with patch.dict(os.environ, {'LHM_ACTIVEPIECES_WEBHOOK_URL':'https://flow.example/webhook/abc'}):
            with patch.object(hub.httpx,'AsyncClient',Client):
                r=self.c.post(f'/tool-jobs/{ident}/execute')
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['receipt']['confirmation'],'workflow_triggered_unverified')

    def test_agent_output_provenance_and_office_tool_count(self):
        # Cannot claim an uncompleted AI text was a completed Agent result.
        t=self.c.post('/workspaces/demo/tasks',json={'role':'content','instruction':'Viết bài giới thiệu dự án để đăng website'}).json()['id']
        bad=self.create(action='wordpress.create_post',p={'title':'T','content':'T','source_task_id':t},idem='provenance-bad')
        self.assertEqual(bad.status_code,409)  # source exists but is not yet cleared by QA
        with core.db() as c:
            c.execute("UPDATE tasks SET status='completed',result='Bản nháp AI có nguồn' WHERE id=?",(t,))
            import publication_gate as pg
            c.execute("""INSERT INTO qa_reviews(qa_task_id,workspace_id,source_task_id,source_digest,role,verdict)
                         VALUES(?,?,?,?,?,?)""",
                      (core.new_id(),self.ws,t,pg.digest('Bản nháp AI có nguồn'),'quality','pass'))
        good=self.create(action='wordpress.create_post',p={'title':'T','content':'Bản nháp AI có nguồn','source_task_id':t},idem='provenance-good')
        self.assertEqual(good.status_code,201,good.text)
        self.assertTrue(any(x['id']==t for x in self.c.get('/workspaces/demo/tools/ready-tasks').json()['drafts']))
        summary=self.c.get('/api/office/snapshot?workspace_id=demo').json()['tool_summary']
        self.assertGreaterEqual(summary['counts']['awaiting_approval'],1)

    def test_all_actions_are_approval_only(self):
        for action in hub.TOOL_ACTIONS:
            self.assertTrue(hub.TOOL_ACTIONS[action]['writes_external'])
            self.assertTrue(hub.TOOL_ACTIONS[action]['approval_required'])

if __name__=='__main__': unittest.main()