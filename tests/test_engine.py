import copy
import csv
import io
import json
import time
import unittest
from pathlib import Path
from engine import Engine

ROOT=Path(__file__).resolve().parents[1]
def load(n): return json.loads((ROOT/'data'/n).read_text())
class CareerTests(unittest.TestCase):
    def setUp(self):
        self.employees=load('employees.json')['employees']; self.events=load('events.json')['events']; self.skills=load('skills.json')
        self.history=list(csv.DictReader(io.StringIO((ROOT/'data/activity_history.csv').read_text())))
        self.eng=Engine(self.employees,self.events,self.skills,self.history)
    def test_growth_never_reduces_and_caps(self):
        event={'develops_skills':[{'skill_id':'A','gain':1,'max_level':3}]}
        self.assertEqual(Engine.apply({'A':5},event)['A'],5)
        self.assertEqual(Engine.apply({'A':2},event)['A'],3)
    def test_only_post_review_history_is_applied_once(self):
        e=copy.deepcopy(self.employees[0]);e['skills']={'SK_SYSTEM_DESIGN':0};e['last_review_date']='2026-09-01'
        history=[{'record_id':str(i),'employee_id':e['employee_id'],'event_id':'EV_005','date':d,'status':'completed'} for i,d in enumerate(['2026-09-02','2026-09-03'])]
        eng=Engine([e],self.events,self.skills,history)
        self.assertEqual(eng.levels(e)['SK_SYSTEM_DESIGN'],1)
    def test_duplicate_completion_across_review_does_not_grant_again(self):
        e=copy.deepcopy(self.employees[1]);e['last_review_date']='2026-09-01';e['skills']['SK_SYSTEM_DESIGN']=2
        history=[{'record_id':str(i),'employee_id':e['employee_id'],'event_id':'EV_005','date':d,'status':'completed'} for i,d in enumerate(['2026-08-01','2026-09-20'])]
        self.assertEqual(Engine([e],self.events,self.skills,history).levels(e)['SK_SYSTEM_DESIGN'],2)

    def test_all_profiles_follow_constraints(self):
        started=time.perf_counter()
        for e in self.employees:
            p=self.eng.profile(e['employee_id'])
            self.assertTrue(0<=p['readiness']<=100)
            for c in p['recommendations']:
                self.assertFalse(self.eng.candidate(e,c,p['levels'],self.eng.records(e['employee_id'])))
                self.assertGreater(c['delta'],0)
                self.assertGreaterEqual(len(c['explanation']),3)
                self.assertFalse(c['mandatory'])
                self.assertIn('history_counts',c)
                for change in c['changes']:
                    self.assertEqual(change['gain'],change['after']-change['before'])
                    self.assertLessEqual(change['after'],change['max_level'])
        print(f'200 profiles: {time.perf_counter()-started:.3f}s')
    def test_jury_trap_critical_skill_beats_smallest_skill(self):
        e=copy.deepcopy(self.employees[1]);e['skills']['SK_SYSTEM_DESIGN']=2;e['skills']['SK_PUBLIC_SPEAKING']=0
        history=[{'record_id':str(i),'employee_id':e['employee_id'],'event_id':'EV_036','date':'2026-09-01','status':'no_show'} for i in range(3)]
        eng=Engine([e],self.events,self.skills,history)
        p=eng.profile(e['employee_id']); first=p['recommendations'][0]
        self.assertTrue(any(c['skill_id']=='SK_SYSTEM_DESIGN' for c in first['changes']),first['title'])
    def test_completed_course_excluded(self):
        e=self.employees[1];history=[{'record_id':'X','employee_id':e['employee_id'],'event_id':'EV_005','date':'2026-08-01','status':'completed'}]
        eng=Engine([e],self.events,self.skills,history)
        self.assertNotIn('EV_005',[x['event_id'] for x in eng.profile(e['employee_id'])['recommendations']])
    def test_participation_model_learns_personal_history(self):
        e=self.employees[1]; event=self.events[4]
        def score(status):
            history=[{'record_id':str(i),'employee_id':e['employee_id'],'event_id':event['event_id'],'date':'2026-09-01','status':status} for i in range(6)]
            return Engine([e],self.events,self.skills,history).participation(e,event)
        for status in ('no_show','declined','dropped'):
            self.assertGreater(score('completed'),score(status))
    def test_rotation_uses_target_role(self):
        p=self.eng.profile('E0004');self.assertEqual(p['target']['role'],'Product Manager')
    def test_time_budget_and_no_steps(self):
        p=self.eng.profile('E0002',1);self.assertEqual(p['recommendations'],[]);self.assertTrue(p['no_step_reason'])
    def test_simulation_recomputes_progress(self):
        e=self.employees[1];p=self.eng.profile(e['employee_id']);c=p['recommendations'][0]
        history=self.history+[{'record_id':'DEMO','employee_id':e['employee_id'],'event_id':c['event_id'],'date':'2026-10-01','status':'completed'}]
        after=Engine(self.employees,self.events,self.skills,history).profile(e['employee_id'])
        self.assertEqual(after['readiness'],c['readiness_after'])
    def test_lead_without_goal_uses_current_grade(self):
        p=self.eng.profile('E0006');self.assertEqual(p['target']['grade'],'Lead');self.assertIsNone(p['next_grade'])

    def test_profile_exposes_next_grade_and_completed_activities(self):
        p=self.eng.profile('E0002')
        self.assertEqual(p['next_grade'],'Senior')
        self.assertEqual(p['completed_count'],len(p['completed_activities']))

if __name__=='__main__': unittest.main()
