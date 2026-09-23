import copy
import http.cookiejar
import json
import tempfile
import threading
import unittest
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
    def test_role_separation(self):
        self.assertEqual(self.request('/api/hr')[0],403);self.login()
        self.assertEqual(self.request('/api/hr')[0],403)
        self.assertEqual(self.request('/api/profile?id=E0001')[0],403)
        self.assertEqual(self.request('/api/import',{'employees':[]})[0],403)
        self.assertEqual(self.login('hr')[0],200);self.assertEqual(self.request('/api/hr')[0],200)
    def test_complete_and_duplicate(self):
        self.login();_,p=self.request('/api/profile');c=p['recommendations'][0]
        self.assertEqual(self.request('/api/complete',{'event_id':c['event_id']})[0],200)
        _,after=self.request('/api/profile');self.assertEqual(after['readiness'],c['readiness_after'])
        self.assertEqual(self.request('/api/complete',{'event_id':c['event_id']})[0],400)
        self.assertTrue(server.STATE.exists())
    def test_import_jury_and_atomic_rejection(self):
        self.login('hr');e=copy.deepcopy(server.DATA['employees'][1]);e['employee_id']='JURY_001'
        _,result=self.request('/api/import',{'employees':{'employees':[e]},'history':'record_id,employee_id,event_id,date,status\nJ1,JURY_001,EV_036,2026-09-01,no_show\n'})
        self.assertEqual(result['employees'],1);self.assertEqual(self.request('/api/profile?id=JURY_001')[0],200)
        before=copy.deepcopy(server.DATA);e['skills']['SK_PYTHON']=99
        self.assertEqual(self.request('/api/import',{'employees':[e]})[0],400)
        self.assertEqual(server.DATA,before)

if __name__=='__main__':unittest.main()
