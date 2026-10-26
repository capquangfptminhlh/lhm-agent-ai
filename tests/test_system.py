"""Offline integration tests. No real DeepSeek API calls."""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),".."))
sys.path.insert(0,ROOT)
_tmp=tempfile.TemporaryDirectory(prefix="lhm_agent_system_")
os.environ["LHM_DB_PATH"]=os.path.join(_tmp.name,"test.sqlite3")
os.environ["LHM_ADMIN_TOKEN"]=""
from fastapi.testclient import TestClient
import app as service
import core


class AgentSystemTest(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(service.app)
        self.ws="test_"+core.new_id().replace("-","")[:16]
        r=self.client.post('/workspaces',json={"id":self.ws,"name":"Demo Commerce","industry":"ecommerce","budget_usd":1.0})
        self.assertEqual(r.status_code,201,r.text)

    def create_task(self,**kwargs):
        data={"role":"content","instruction":"Viết bài giới thiệu sản phẩm để đăng website"}
        data.update(kwargs)
        return self.client.post(f'/workspaces/{self.ws}/tasks',json=data)

    def test_catalog_and_health(self):
        self.assertEqual(self.client.get('/catalog').status_code,200)
        self.assertEqual(len(self.client.get('/catalog').json()['roles']),16)
        self.assertIn('real_estate',self.client.get('/catalog').json()['industries'])
        self.assertIn('registered_agent_roles',self.client.get('/metrics?workspace_id='+self.ws).json())
        self.assertIn('<!doctype html>',self.client.get('/dashboard').text)
        self.assertIn('active',self.client.get('/state').json())

    def test_no_key_never_pretends_success(self):
        t=self.create_task().json()['id']
        with patch.dict(os.environ,{"DEEPSEEK_API_KEY":""}):
            x=self.client.post(f'/tasks/{t}/run')
            self.assertEqual(x.status_code,503)
            self.assertEqual(self.client.get(f'/tasks/{t}').json()['status'],'queued')
            y=self.client.post('/v1/chat/completions',json={'messages':[{'role':'user','content':'Xin chào'}]})
            self.assertEqual(y.status_code,503)

    def test_idempotency_one_task(self):
        payload={'idempotency_key':'once-001'}
        a=self.create_task(**payload)
        b=self.create_task(**payload)
        self.assertEqual(a.json()['id'],b.json()['id'])
        self.assertTrue(b.json()['deduplicated'])

    def test_dynamic_role_router(self):
        self.assertEqual(self.create_task(role=None,instruction="Tìm khách tiềm năng qua nguồn công khai").json()['role'],'lead')
        self.assertEqual(self.create_task(role=None,instruction="Viết bài SEO cho sản phẩm").json()['role'],'content')

    def test_human_approval_gate(self):
        t=self.create_task(requires_approval=True).json()['id']
        self.assertEqual(self.client.get('/tasks/'+t).json()['status'],'awaiting_approval')
        self.assertEqual(self.client.post('/tasks/'+t+'/approve').json()['status'],'queued')
        self.assertEqual(self.client.post('/tasks/'+t+'/approve').status_code,409)

    def test_budget_block_blocks_provider(self):
        with core.db() as c:
            c.execute('UPDATE workspaces SET budget_usd=0.001 WHERE id=?',(self.ws,))
        t=self.create_task().json()['id']
        with patch.dict(os.environ,{"DEEPSEEK_API_KEY":"TEST-KEY"}):
            with patch.object(service,'deepseek_request') as call:
                r=self.client.post('/tasks/'+t+'/run')
            self.assertEqual(r.status_code,402,r.text)
            call.assert_not_called()
        self.assertEqual(self.client.get('/tasks/'+t).json()['status'],'blocked_budget')

    def test_campaign_dependency(self):
        x=self.client.post(f'/workspaces/{self.ws}/campaigns',json={"objective":"Tăng khách hàng đăng ký dùng sản phẩm trong 30 ngày","mode":"lean"})
        self.assertEqual(x.status_code,201,x.text)
        pipeline=x.json()['pipeline']
        self.assertEqual(len(pipeline),3)
        deps=[self.client.get('/tasks/'+x['id']).json()['depends_on'] for x in pipeline]
        self.assertEqual(deps,[None,pipeline[0]['id'],pipeline[1]['id']])
        with patch.dict(os.environ,{"DEEPSEEK_API_KEY":"TEST-KEY"}):
            self.assertEqual(self.client.post('/tasks/'+pipeline[1]['id']+'/run').status_code,409)

    def test_real_provider_shape_and_actual_usage_recorded(self):
        t=self.create_task().json()['id']
        async def mocked(role,ws,instruction,context,model):
            self.assertEqual(ws['industry'],'ecommerce')
            self.assertEqual(model,'deepseek-flash')
            return {"choices":[{"message":{"content":"Kế hoạch mẫu, chưa đăng lên web"}}],
                    "usage":{"prompt_tokens":500,"prompt_cache_hit_tokens":200,"completion_tokens":300}}
        with patch.dict(os.environ,{"DEEPSEEK_API_KEY":"TEST-KEY"}):
            with patch.object(service,'deepseek_request',side_effect=mocked):
                r=self.client.post('/tasks/'+t+'/run')
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['status'],'completed')
        self.assertGreater(r.json()['cost_upper_usd'],0)
        self.assertEqual(self.client.post('/tasks/'+t+'/run').status_code,409)
        metric=self.client.get('/metrics?workspace_id='+self.ws).json()
        self.assertEqual(metric['tokens']['hit'],200)
        self.assertEqual(metric['tokens']['miss'],300)
        self.assertEqual(metric['tokens']['output'],300)
        self.assertEqual(metric['financials']['reserved_usd'],0)

    def test_retries_and_refunds_budget_reservation(self):
        t=self.create_task().json()['id']
        async def fail(*args):
            raise service.HTTPException(503,'DeepSeek temporarily unavailable')
        with patch.dict(os.environ,{"DEEPSEEK_API_KEY":"TEST-KEY"}):
            with patch.object(service,'deepseek_request',side_effect=fail):
                r=self.client.post('/tasks/'+t+'/run')
        self.assertEqual(r.status_code,503)
        task=self.client.get('/tasks/'+t).json()
        self.assertEqual(task['status'],'queued')
        self.assertEqual(task['attempts'],1)
        self.assertGreater(task['next_run_at'],core.now())
        self.assertEqual(self.client.get('/metrics?workspace_id='+self.ws).json()['financials']['reserved_usd'],0)

    def test_import_only_public_business_lead_with_source(self):
        payload={"business_name":"Cửa hàng A","source_url":"https://example.com/a","fit":8,"intent":5,"reachable":10,"data_origin_confirmed":True}
        no={**payload,"data_origin_confirmed":False}
        self.assertEqual(self.client.post(f'/workspaces/{self.ws}/leads',json=no).status_code,422)
        good=self.client.post(f'/workspaces/{self.ws}/leads',json=payload)
        self.assertEqual(good.status_code,201,good.text)
        self.assertEqual(good.json()['score'],72)
        self.assertFalse(good.json()['verified'])
        self.assertEqual(self.client.post(f'/workspaces/{self.ws}/leads',json=payload).status_code,409)

    def test_universal_workflows_and_custom_industry(self):
        cid="industry_"+core.new_id().replace('-','')[:10]
        p=self.client.post('/industries',json={"id":cid,"label":"Dịch vụ thú cưng", "audience":"Chủ thú cưng cần dịch vụ thường xuyên", "proof":"Không bịa chứng chỉ thú y hoặc hiệu quả điều trị", "channels":["Website","Google Maps"]})
        self.assertEqual(p.status_code,201,p.text)
        w=self.client.post('/workspaces',json={"id":"pet_"+cid,"name":"Pet Store","industry":cid,"budget_usd":3})
        self.assertEqual(w.status_code,201,w.text)
        work=self.client.post(f'/workspaces/{self.ws}/workflows',json={"process":"procurement","objective":"So sánh báo giá nhập phụ kiện, không tự đặt hàng"})
        self.assertEqual(work.status_code,201,work.text)
        self.assertEqual(len(work.json()['pipeline']),3)
        self.assertIn('Kiểm soát & QA',self.client.get('/catalog').json()['departments'])

    def test_offpeak_scheduling_utc(self):
        from datetime import datetime,timezone
        tm=lambda h: int(datetime(2026,10,26,h,25,tzinfo=timezone.utc).timestamp())
        self.assertEqual(core.next_offpeak(tm(2)),int(datetime(2026,10,26,4,0,tzinfo=timezone.utc).timestamp()))
        self.assertEqual(core.next_offpeak(tm(7)),int(datetime(2026,10,26,10,0,tzinfo=timezone.utc).timestamp()))
        self.assertEqual(core.next_offpeak(tm(11)),tm(11))

    def test_sequential_workflow_actual_outputs(self):
        x=self.client.post(f'/workspaces/{self.ws}/workflows',json={"process":"finance","objective":"Phân tích chi phí đầu vào và kiểm soát chất lượng"})
        steps=x.json()['pipeline']
        seen=[]
        async def mocked(role,ws,instruction,context,model):
            seen.append((role,context))
            return {"choices":[{"message":{"content":"OUTPUT-"+role}}],"usage":{"prompt_tokens":100,"completion_tokens":200}}
        with patch.dict(os.environ,{"DEEPSEEK_API_KEY":"TEST-KEY"}):
            with patch.object(service,'deepseek_request',side_effect=mocked):
                self.assertEqual(self.client.post('/tasks/'+steps[0]['id']+'/run').status_code,200)
                self.assertEqual(self.client.post('/tasks/'+steps[1]['id']+'/run').status_code,200)
        self.assertEqual([x[0] for x in seen],['finance','quality'])
        self.assertIn('OUTPUT-finance',seen[1][1])

    def test_worker_propagates_failed_dependency(self):
        import worker
        x=self.client.post(f'/workspaces/{self.ws}/workflows',json={"process":"finance","objective":"Phân tích chi phí sản xuất của công ty"})
        ids=[v['id'] for v in x.json()['pipeline']]
        with core.db() as c:
            c.execute("UPDATE tasks SET status='failed' WHERE id=?",(ids[0],))
        import asyncio
        asyncio.run(worker.recover_stuck())
        self.assertEqual(self.client.get('/tasks/'+ids[1]).json()['status'],'blocked_dependency')

    def test_pro_model_opt_in(self):
        self.assertEqual(self.create_task(complexity="high").status_code,422)



    def test_office_assets_and_live_roster(self):
        self.assertEqual(self.client.get('/office').status_code,200)
        self.assertIn('office-scene', self.client.get('/office').text)
        self.assertIn('renderRooms',self.client.get('/office/office.js').text)
        self.assertEqual(self.client.get('/office/office.css').status_code,200)
        snapshot=self.client.get('/api/office/snapshot?workspace_id='+self.ws)
        self.assertEqual(snapshot.status_code,200,snapshot.text)
        data=snapshot.json()
        self.assertEqual(len(data['agents']),16)
        self.assertEqual(len(data['departments']),8)
        self.assertEqual(data['agents']['research']['status'],'idle')
        self.assertEqual(data['tasks'],[])
        self.assertFalse(data['worker']['online'])

    def test_office_status_always_comes_from_task(self):
        r=self.create_task(role='research',instruction='Nghiên cứu thị trường mới có nguồn kiểm chứng',requires_approval=True)
        tid=r.json()['id']
        endpoint='/api/office/snapshot?workspace_id='+self.ws
        first=self.client.get(endpoint).json()['agents']['research']
        self.assertEqual(first['status'],'awaiting_approval')
        self.assertEqual(first['task_id'],tid)
        self.client.post('/tasks/'+tid+'/approve')
        self.assertEqual(self.client.get(endpoint).json()['agents']['research']['status'],'queued')
        async def mock_ai(*args):
            return {'choices':[{'message':{'content':'Kết quả thử nghiệm AI, không phải thị trường thật'}}],
                    'usage':{'prompt_tokens':110,'completion_tokens':170}}
        with patch.dict(os.environ,{'DEEPSEEK_API_KEY':'TEST'}):
            with patch.object(service,'deepseek_request',side_effect=mock_ai):
                response=self.client.post('/tasks/'+tid+'/run')
        self.assertEqual(response.status_code,200,response.text)
        final=self.client.get(endpoint).json()
        self.assertEqual(final['agents']['research']['status'],'completed')
        self.assertEqual(final['agents']['research']['completed'],1)
        self.assertGreater(final['financials']['recorded_upper_cost_usd'],0)

    def test_office_workspace_isolation_and_invalid_ws(self):
        other='off_'+core.new_id().replace('-','')[:12]
        self.client.post('/workspaces',json={'id':other,'name':'Khác','industry':'general','budget_usd':1})
        self.create_task(role='content',instruction='Viết bài riêng cho workspace A')
        a=self.client.get('/api/office/snapshot?workspace_id='+self.ws).json()
        b=self.client.get('/api/office/snapshot?workspace_id='+other).json()
        self.assertEqual(len(a['tasks']),1)
        self.assertEqual(len(b['tasks']),0)
        self.assertEqual(self.client.get('/api/office/snapshot?workspace_id=nonexistent').status_code,404)

    def test_worker_heartbeat_reflected_not_faked(self):
        with core.db() as c:
            c.execute("INSERT OR REPLACE INTO worker_heartbeats(id,updated_at) VALUES('default',?)",(core.now(),))
        self.assertTrue(self.client.get('/api/office/snapshot?workspace_id='+self.ws).json()['worker']['online'])
        with core.db() as c:
            c.execute("UPDATE worker_heartbeats SET updated_at=? WHERE id='default'",(core.now()-999,))
        self.assertFalse(self.client.get('/api/office/snapshot?workspace_id='+self.ws).json()['worker']['online'])

if __name__=='__main__': unittest.main(verbosity=2)