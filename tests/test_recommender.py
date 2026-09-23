"""Behavior checks against the actual Career Quest catalog; stdlib only."""

import copy
import csv
import json
from pathlib import Path
import unittest

from recommender import apply_activity, career_progress, effective_skills, recommend


DATA = Path(__file__).resolve().parents[1] / "career_quest_dataset"
TODAY = "2026-10-01"


class RecommenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.employees = json.loads((DATA / "employees.json").read_text())["employees"]
        cls.events = json.loads((DATA / "events.json").read_text())["events"]
        cls.profiles = json.loads((DATA / "skills.json").read_text())["role_profiles"]
        cls.by_id = {event["event_id"]: event for event in cls.events}
        with (DATA / "activity_history.csv").open(newline="") as stream:
            cls.history = list(csv.DictReader(stream))

    def employee(self):
        return copy.deepcopy(next(row for row in self.employees
                                  if row["role"] == "Backend Engineer" and row["grade"] == "Middle"))

    def row(self, employee, event_id, status="completed", when="2026-09-15", record_id="T1"):
        return {
            "record_id": record_id, "employee_id": employee["employee_id"],
            "event_id": event_id, "date": when, "status": status,
            "due_date": "", "completion_pct": 100 if status == "completed" else 0,
            "score": "", "feedback_rating": "", "assigned_by": "self",
        }

    def test_actual_dataset_determinism_and_completion_improves_readiness(self):
        for employee in self.employees:
            with self.subTest(employee=employee["employee_id"]):
                skills = effective_skills(employee, self.history, self.events, TODAY)
                progress = career_progress(employee, skills, self.profiles)
                recommendations = recommend(employee, skills, self.history, self.events, self.profiles, TODAY)
                self.assertLessEqual(len(recommendations), 3)
                self.assertEqual(recommendations, recommend(
                    employee, skills, self.history, self.events, self.profiles, TODAY))
                for item in recommendations:
                    event = item["event"]
                    self.assertFalse(event["mandatory"])
                    self.assertIn(employee["role"], event["target_roles"])
                    self.assertIn(employee["grade"], event["target_grades"])
                    self.assertTrue(all(skills.get(key, 0) >= value
                                        for key, value in event["prerequisites"].items()))
                    self.assertGreaterEqual(item["score"], 0)
                    self.assertLessEqual(item["score"], 100)
                    self.assertTrue(item["impacts"])
                    updated = apply_activity(skills, event)
                    self.assertGreater(career_progress(employee, updated, self.profiles)["readiness"],
                                       progress["readiness"])

    def test_gain_cap_missing_skill_and_no_mutation(self):
        original = {"SK_SYSTEM_DESIGN": 2, "SK_API_DESIGN": 5}
        updated = apply_activity(original, self.by_id["EV_005"])
        self.assertEqual(updated["SK_SYSTEM_DESIGN"], 3)
        self.assertEqual(updated["SK_API_DESIGN"], 5)
        self.assertEqual(original, {"SK_SYSTEM_DESIGN": 2, "SK_API_DESIGN": 5})
        self.assertEqual(apply_activity({}, self.by_id["EV_036"])["SK_PUBLIC_SPEAKING"], 1)

    def test_history_only_after_review_before_cutoff_and_once(self):
        employee = self.employee()
        employee["skills"] = {"SK_PUBLIC_SPEAKING": 0}
        employee["last_review_date"] = "2026-09-01"
        records = [self.row(employee, "EV_036", when=when, record_id=str(index))
                   for index, when in enumerate(("2026-08-01", "2026-09-01", "2026-09-15", "2026-10-15"))]
        records.append(copy.deepcopy(records[2]))  # Duplicate upload must not grant twice.
        records.append(self.row(employee, "EV_036", status="dropped", record_id="D"))
        other_employee = self.row(employee, "EV_036", record_id="OTHER")
        other_employee["employee_id"] = "someone_else"
        records.append(other_employee)
        result = effective_skills(employee, list(reversed(records)), self.events, TODAY)
        self.assertEqual(result["SK_PUBLIC_SPEAKING"], 1)
        self.assertEqual(employee["skills"]["SK_PUBLIC_SPEAKING"], 0)
        del employee["last_review_date"]
        self.assertEqual(effective_skills(employee, records, self.events, TODAY), employee["skills"])

    def test_completed_and_in_progress_filters_repeatable_exception(self):
        employee = self.employee()
        skills = {"SK_SYSTEM_DESIGN": 2, "SK_PUBLIC_SPEAKING": 0}
        events = [self.by_id["EV_005"], self.by_id["EV_006"], self.by_id["EV_036"]]
        records = [self.row(employee, "EV_005"), self.row(employee, "EV_006", "in_progress", record_id="T2"),
                   self.row(employee, "EV_036", record_id="T3")]
        result = recommend(employee, skills, records, events, self.profiles, TODAY)
        self.assertEqual([item["event"]["event_id"] for item in result], ["EV_036"])
        records.append(self.row(employee, "EV_006", "dropped", "2026-09-16", "T4"))
        self.assertIn("EV_006", [item["event"]["event_id"] for item in recommend(
            employee, skills, records, events, self.profiles, TODAY)])

    def test_prerequisites_schedule_and_saturated_caps(self):
        employee = self.employee()
        self.assertEqual(recommend(employee, {"SK_SYSTEM_DESIGN": 1}, [], [self.by_id["EV_006"]],
                                   self.profiles, TODAY), [])
        self.assertEqual(recommend(employee, {"SK_SYSTEM_DESIGN": 2}, [], [self.by_id["EV_006"]],
                                   self.profiles, "2027-01-01"), [])
        self.assertEqual(recommend(employee, {"SK_SYSTEM_DESIGN": 3, "SK_API_DESIGN": 3}, [],
                                   [self.by_id["EV_005"]], self.profiles, TODAY), [])

    def test_critical_gap_and_repeated_skips_influence_priority(self):
        employee = self.employee()
        target = next(profile for profile in self.profiles
                      if profile["role"] == employee["role"] and profile["grade"] == "Senior")
        skills = dict(target["required_skills"])
        skills.update(SK_SYSTEM_DESIGN=2, SK_PUBLIC_SPEAKING=0)
        events = [self.by_id["EV_005"], self.by_id["EV_036"]]
        baseline = {item["event"]["event_id"]: item for item in recommend(
            employee, skills, [], events, self.profiles, TODAY)}
        records = [self.row(employee, "EV_036", status, record_id=str(index))
                   for index, status in enumerate(("no_show", "no_show", "declined"))]
        result = recommend(employee, skills, records, events, self.profiles, TODAY)
        self.assertEqual(result[0]["event"]["event_id"], "EV_005")
        club = next(item for item in result if item["event"]["event_id"] == "EV_036")
        self.assertEqual(club["breakdown"]["skip_penalty"], -18)
        self.assertLess(club["score"], baseline["EV_036"]["score"])
        self.assertGreater(result[0]["breakdown"]["next_grade_relevance"], 0)

    def test_lead_and_all_requirements_met(self):
        employee = self.employee()
        employee["grade"] = "Lead"
        self.assertIsNone(career_progress(employee, {}, self.profiles)["readiness"])
        self.assertEqual(recommend(employee, {}, [], self.events, self.profiles, TODAY), [])
        employee["grade"] = "Middle"
        requirements = career_progress(employee, {}, self.profiles)["requirements"]
        self.assertEqual(career_progress(employee, requirements, self.profiles)["readiness"], 100)
        self.assertEqual(recommend(employee, requirements, [], self.events, self.profiles, TODAY), [])


if __name__ == "__main__":
    unittest.main()
