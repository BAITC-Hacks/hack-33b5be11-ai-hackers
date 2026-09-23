import copy
import json
import os
import unittest
import time
import threading
from types import SimpleNamespace
from unittest.mock import patch

import server
from career_agent import CareerAgent, TOOL_DESCRIPTIONS


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.agent = CareerAgent(server.engine())
        self.eid = 'E0028'

    def fake_client(self, invalid=False, other_employee=False):
        self.requests = []
        def create(**kwargs):
            self.requests.append(copy.deepcopy(kwargs))
            n = len(self.requests)
            # Deliberately multiple rounds, with order chosen by the model fixture.
            names = ['get_activity_history', 'get_employee_profile'] if n == 1 else ['get_recommendations', 'get_next_grade_requirements']
            if n < 3:
                return SimpleNamespace(output=[SimpleNamespace(type='function_call', name=name,
                    arguments=json.dumps({'employee_id': 'E0001' if other_employee else self.eid}), call_id=name)
                    for name in names], output_text='')
            c = self.agent.get_recommendations(self.eid)['activities']
            change = next(x for x in c[0]['changes'] if x['required'] > x['before'])
            return SimpleNamespace(output=[], output_text=json.dumps({
                'event_id': 'INVENTED' if invalid else c[0]['event_id'],
                'skill_id': change['skill_id'], 'alternative_event_id': c[1]['event_id'] if len(c)>1 else None,
                'reason_indices': [1, 2, 0, 3]}))
        return SimpleNamespace(responses=SimpleNamespace(create=create))

    def test_multiround_tools_and_grounded_output(self):
        before=copy.deepcopy(server.DATA)
        r=self.agent.recommend(self.eid,self.fake_client())
        self.assertEqual(r['mode'],'openai')
        self.assertEqual(set(r['tools_used']),set(TOOL_DESCRIPTIONS))
        outputs=[x for x in self.requests[2]['input'] if isinstance(x,dict) and x.get('type')=='function_call_output']
        self.assertEqual(len(outputs),4)
        c=self.agent.get_recommendations(self.eid)['activities'][0]
        self.assertEqual(r['recommended_activity_id'],c['event_id'])
        self.assertIn(r['expected_gain'],[x['gain'] for x in c['changes']])
        self.assertEqual(before,server.DATA)

    def test_hard_deadline_even_if_provider_ignores_timeout(self):
        release=threading.Event();exited=threading.Event()
        def slow(**kwargs):
            try:
                release.wait(2)
                raise TimeoutError()
            finally: exited.set()
        client=SimpleNamespace(responses=SimpleNamespace(create=slow))
        try:
            with patch('career_agent.AI_BUDGET_SECONDS',0.05):
                started=time.monotonic();r=self.agent.recommend(self.eid,client)
            self.assertLess(time.monotonic()-started,0.5)
            self.assertEqual(r['fallback_reason'],'deadline_exceeded')
        finally:
            release.set();exited.wait(2)

    def test_saturation_returns_immediate_fallback(self):
        with patch('career_agent.AI_WORKERS', threading.BoundedSemaphore(0)):
            r=self.agent.recommend(self.eid,self.fake_client())
        self.assertEqual(r['fallback_reason'],'busy')

    def test_no_key(self):
        with patch.dict(os.environ,{'OPENAI_API_KEY':''}):
            r=self.agent.recommend(self.eid)
        self.assertEqual(r['mode'],'fallback');self.assertEqual(r['tools_used'],[])
        self.assertEqual(r['recommendations'],server.engine().profile(self.eid)['recommendations'])

    def test_invalid_selection_and_cross_employee(self):
        self.assertEqual(self.agent.recommend(self.eid,self.fake_client(invalid=True))['mode'],'fallback')
        r=self.agent.recommend(self.eid,self.fake_client(other_employee=True))
        self.assertEqual(r['mode'],'fallback');self.assertEqual(r['tools_used'],[])

    def test_timeout_and_secret_redaction(self):
        client=SimpleNamespace(responses=SimpleNamespace(create=lambda **k: (_ for _ in ()).throw(TimeoutError('SECRET_SENTINEL'))))
        r=self.agent.recommend(self.eid,client)
        self.assertEqual(r['mode'],'fallback');self.assertNotIn('SECRET_SENTINEL',json.dumps(r))

    def test_requirements_history_and_empty_catalog(self):
        e=self.agent.engine.employees[self.eid]
        req=self.agent.get_next_grade_requirements(self.eid)
        self.assertEqual(req['requirements'],self.agent.engine.profiles[(e['role'],self.agent.engine.next_grade(e))]['required_skills'])
        self.assertEqual(self.agent.get_activity_history(self.eid)['records'],self.agent.engine.records(self.eid))
        self.agent.hours=0
        with patch.dict(os.environ,{'OPENAI_API_KEY':''}):r=self.agent.recommend(self.eid)
        self.assertIsNone(r['recommended_activity']);self.assertIsNone(r['expected_gain'])

if __name__=='__main__':unittest.main()
