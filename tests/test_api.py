import copy
import http.cookiejar
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from career_agent import CareerAgent, TOOL_DESCRIPTIONS
import urllib.request
import urllib.error
from pathlib import Path
from http.server import ThreadingHTTPServer
import server

class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_data=copy.deepcopy(server.DATA);cls.original_state=server.STATE
        cls.temp=tempfile.TemporaryDirectory();server.STATE=Path(cls.temp.name)/'state.json'
        cls.http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        cls.url='http://127.0.0.1:'+str(cls.http.server_port)
        cls.thread=threading.Thread(target=cls.http.serve_forever,daemon=True);cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown();cls.http.server_close();server.DATA=cls.original_data;server.STATE=cls.original_state;cls.temp.cleanup()
    def setUp(self):
        server.DATA=copy.deepcopy(self.original_data)
        self.client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    def request(self,path,data=None):
        req=urllib.request.Request(self.url+path,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json'})
        try:
            with self.client.open(req) as r: return r.status,json.load(r)
        except urllib.error.HTTPError as e:return e.code,json.load(e)
    def login(self,role='employee'):
        return self.request('/api/login',{'role':role,'employee_id':'E0002','password':'hackalem-demo'})
    def test_health_and_bom_history(self):
        status,health=self.request('/api/health')
        self.assertEqual(status,200);self.assertEqual(len(health['build']),10)
        self.assertNotIn('api_key',health)
        self.login('hr')
        status,result=self.request('/api/import',{'history':'\ufeffrecord_id,employee_id,event_id,date,status\nBOM_QA,E0002,EV_036,2026-09-01,no_show\n'})
        self.assertEqual(status,200);self.assertEqual(result['history'],1)

    def test_role_separation(self):
        self.assertEqual(self.request('/api/hr')[0],403);self.login()
        self.assertEqual(self.request('/api/hr')[0],403)
        self.assertEqual(self.request('/api/profile?id=E0001')[0],403)
        self.assertEqual(self.request('/api/import',{'employees':[]})[0],403)
        self.assertEqual(self.login('hr')[0],200);self.assertEqual(self.request('/api/hr')[0],200)
    def test_https_cookie_configuration(self):
        payload={'role':'employee','employee_id':'E0002'}
        for secure in ('0','1'):
            with self.subTest(secure=secure), patch.dict('os.environ',{'COOKIE_SECURE':secure}):
                req=urllib.request.Request(self.url+'/api/login',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
                with urllib.request.urlopen(req) as response:
                    cookie=response.headers['Set-Cookie']
                self.assertEqual('; Secure' in cookie,secure=='1')
                self.assertIn('HttpOnly',cookie)
                self.assertIn('SameSite=Strict',cookie)
    def test_agent_endpoint_fallback_and_access(self):
        self.assertEqual(self.request('/api/agent/recommend',{'employee_id':'E0028'})[0],403)
        self.login()
        self.assertEqual(self.request('/api/agent/recommend',{'employee_id':'E0028'})[0],403)
        with patch.dict('os.environ',{'OPENAI_API_KEY':''}):
            status,result=self.request('/api/agent/recommend',{'employee_id':'E0002','hours':8})
        self.assertEqual(status,200);self.assertEqual(result['mode'],'fallback')
        self.assertEqual(result['tools_used'],[])
        self.assertEqual(result['recommendations'],server.engine().profile('E0002',8)['recommendations'])
        self.assertEqual(self.request('/api/agent/recommend',{})[0],400)
        self.login('hr')
        self.assertEqual(self.request('/api/agent/recommend',{'employee_id':'UNKNOWN'})[0],400)

    def test_complete_and_duplicate(self):
        self.login();_,p=self.request('/api/profile');c=p['recommendations'][0]
        self.assertEqual(self.request('/api/complete',{'event_id':c['event_id']})[0],200)
        _,after=self.request('/api/profile');self.assertEqual(after['readiness'],c['readiness_after'])
        self.assertEqual(self.request('/api/complete',{'event_id':c['event_id']})[0],400)
        self.assertEqual(self.request('/api/complete',{'event_id':'EV_001'})[0],400)
        self.assertTrue(server.STATE.exists())
    def test_import_jury_and_atomic_rejection(self):
        self.login('hr');e=copy.deepcopy(server.DATA['employees'][1]);e['employee_id']='JURY_001'
        _,result=self.request('/api/import',{'employees':{'employees':[e]},'history':'record_id,employee_id,event_id,date,status\nJ1,JURY_001,EV_036,2026-09-01,no_show\n'})
        self.assertEqual(result['employees'],1);self.assertEqual(self.request('/api/profile?id=JURY_001')[0],200)
        before=copy.deepcopy(server.DATA);e['skills']['SK_PYTHON']=99
        self.assertEqual(self.request('/api/import',{'employees':[e]})[0],400)
        self.assertEqual(server.DATA,before)
        self.assertEqual(self.request('/api/import',{'history':'employee_id,event_id\nE0002,EV_036\n'})[0],400)

    def test_judge_sample_import_profile_agent_and_complete(self):
        directory=Path(__file__).parent/'judge_sample'
        employees=json.loads((directory/'employees.json').read_text())
        history=(directory/'activity_history.csv').read_text()
        eid=employees['employees'][0]['employee_id']
        # Run from a clean working state even if the sample was imported locally.
        server.DATA['employees']=[e for e in server.DATA['employees'] if e['employee_id']!=eid]
        server.DATA['history']=[r for r in server.DATA['history'] if r['employee_id']!=eid]
        self.login('hr')
        self.assertEqual(self.request('/api/profile?id='+eid)[0],400)
        status,result=self.request('/api/import',{'employees':employees,'history':history})
        self.assertEqual(status,200);self.assertEqual(result['employee_ids'],[eid])
        _,available=self.request('/api/demo')
        self.assertIn(eid,[e['employee_id'] for e in available['employees']])
        status,p=self.request('/api/profile?id='+eid)
        self.assertEqual(status,200)
        self.assertEqual(p,server.engine().profile(eid))
        self.assertEqual(p['employee']['grade'],'Middle');self.assertEqual(p['next_grade'],'Senior')
        self.assertTrue(any(g['gap']>0 for g in p['gaps']))
        self.assertGreaterEqual(len(p['recommendations']),1);self.assertLessEqual(len(p['recommendations']),3)
        self.assertIsInstance(p['readiness'],(int,float))
        self.assertEqual({r['status'] for r in p['history']},{'completed','no_show','declined'})
        club=next(c for c in p['recommendations'] if c['event_id']=='EV_036')
        self.assertEqual(club['history_counts']['no_show'],1)
        self.assertEqual(club['history_counts']['declined'],1)
        calls=[]
        def create(**kwargs):
            calls.append(kwargs)
            if len(calls)==1:
                return SimpleNamespace(output=[SimpleNamespace(type='function_call',name=name,
                    arguments=json.dumps({'employee_id':eid}),call_id=name) for name in TOOL_DESCRIPTIONS],output_text='')
            c=p['recommendations'][0]
            skill=next(x for x in c['changes'] if x['required']>x['before'])
            return SimpleNamespace(output=[],output_text=json.dumps({'event_id':c['event_id'],
                'skill_id':skill['skill_id'],'alternative_event_id':p['recommendations'][1]['event_id'],
                'reason_indices':[1,2,0,3]}))
        client=SimpleNamespace(responses=SimpleNamespace(create=create))
        original_recommend=CareerAgent.recommend
        with patch.object(CareerAgent,'recommend',lambda agent,employee_id: original_recommend(agent,employee_id,client)):
            status,agent=self.request('/api/agent/recommend',{'employee_id':eid})
        self.assertEqual(status,200);self.assertEqual(agent['mode'],'openai')
        self.assertEqual(set(agent['tools_used']),set(TOOL_DESCRIPTIONS))
        outputs=[x for x in calls[1]['input'] if isinstance(x,dict) and x.get('type')=='function_call_output']
        self.assertEqual(len(outputs),4)
        self.assertEqual(agent['recommendations'],p['recommendations'])
        with patch.dict('os.environ',{'OPENAI_API_KEY':''}):
            status,fallback=self.request('/api/agent/recommend',{'employee_id':eid})
        self.assertEqual(status,200);self.assertEqual(fallback['recommendations'],p['recommendations'])
        chosen=p['recommendations'][0]
        status,_=self.request('/api/complete',{'employee_id':eid,'event_id':chosen['event_id']})
        self.assertEqual(status,200)
        _,after=self.request('/api/profile?id='+eid)
        self.assertEqual(after['readiness'],chosen['readiness_after'])
        self.assertEqual(after['completed_count'],p['completed_count']+1)
        self.assertEqual(self.request('/api/login',{'role':'employee','employee_id':eid})[0],200)
        status,own=self.request('/api/profile')
        self.assertEqual(status,200);self.assertEqual(own['employee']['employee_id'],eid)

    def test_import_single_minimal_profile(self):
        self.login('hr')
        employee={'employee_id':'JURY_MIN','role':'Backend Engineer','grade':'Middle',
                  'skills':{'SK_SYSTEM_DESIGN':1}}
        status,result=self.request('/api/import',{'employees':employee})
        self.assertEqual(status,200);self.assertEqual(result['employees'],1)
        self.assertEqual(result['employee_ids'],['JURY_MIN'])
        status,profile=self.request('/api/profile?id=JURY_MIN')
        self.assertEqual(status,200);self.assertEqual(profile['employee']['full_name'],'JURY_MIN')
        self.assertEqual(profile['next_grade'],'Senior')

if __name__=='__main__':unittest.main()
