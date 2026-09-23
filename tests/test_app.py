"""Exercise the actual Streamlit app and session transitions without a browser."""

import copy
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from data_loader import load_dataset
from recommender import effective_skills, recommend


APP = str(Path(__file__).resolve().parents[1] / "app.py")


class AppTests(unittest.TestCase):
    def start_app(self):
        app = AppTest.from_file(APP, default_timeout=30).run()
        self.assertFalse(app.exception)
        return app

    def upload(self, app, employee, history=None):
        # Streamlit 1.49 AppTest does not expose a file-uploader input method.
        # Supply its public return value, then exercise the real form submission.
        def uploaded_file(label, **kwargs):
            if label == "Employee JSON":
                return io.BytesIO(json.dumps(employee).encode())
            return io.BytesIO(history) if history is not None else None

        submit = next(button for button in app.button if button.label == "Загрузить и рассчитать")
        with patch("streamlit.file_uploader", side_effect=uploaded_file):
            submit.click().run()
        self.assertFalse(app.exception)

    def test_complete_and_switch_profiles_without_double_gain(self):
        app = self.start_app()
        self.assertEqual([tab.label for tab in app.tabs], ["Employee", "HR Dashboard"])
        app.selectbox(key="employee_selection").select("E0002").run()
        before = copy.deepcopy(app.session_state["cq_skills"]["E0002"])
        history_length = len(app.session_state["cq_history"])
        readiness = next(metric.value for metric in app.metric if metric.label == "Career readiness")
        button = next(button for button in app.button if button.label == "Complete activity")
        completed_key = button.key
        button.click().run()
        self.assertFalse(app.exception)
        after = copy.deepcopy(app.session_state["cq_skills"]["E0002"])
        self.assertNotEqual(before, after)
        self.assertTrue(all(after[key] >= level for key, level in before.items()))
        self.assertEqual(len(app.session_state["cq_history"]), history_length + 1)
        updated_readiness = next(metric.value for metric in app.metric if metric.label == "Career readiness")
        self.assertGreater(float(updated_readiness.rstrip("%")), float(readiness.rstrip("%")))
        self.assertNotIn(completed_key, [button.key for button in app.button])
        for employee_id in ("E0001", "E0050", "E0100", "E0200", "E0002"):
            app.selectbox(key="employee_selection").select(employee_id).run()
            self.assertFalse(app.exception)
        self.assertEqual(app.session_state["cq_skills"]["E0002"], after)
        self.assertEqual(len(app.session_state["cq_history"]), history_length + 1)

    def test_judge_form_same_engine_and_complete(self):
        dataset = load_dataset()
        app = self.start_app()
        employee = copy.deepcopy(dataset["employees"][1])
        employee["employee_id"] = "JUDGE_UPLOAD"
        employee["last_review_date"] = "2026-09-01"
        employee["skills"]["SK_SYSTEM_DESIGN"] = 1
        history = (
            "record_id,employee_id,event_id,date,status,completion_pct\n"
            "JUDGE_R1,JUDGE_UPLOAD,EV_005,2026-09-20,completed,100\n"
            "JUDGE_R2,JUDGE_UPLOAD,EV_036,2026-09-25,no_show,0\n"
        ).encode()
        source_skills = copy.deepcopy(app.session_state["cq_skills"]["E0002"])
        self.upload(app, {"employees": [employee]}, history)
        self.assertEqual(len(app.session_state["cq_employees"]), 201)
        self.assertEqual(app.selectbox(key="employee_selection").value, "JUDGE_UPLOAD")
        self.assertEqual(app.session_state["cq_skills"]["E0002"], source_skills)
        self.assertEqual(app.session_state["cq_skills"]["JUDGE_UPLOAD"]["SK_SYSTEM_DESIGN"], 2)
        expected_skills = effective_skills(employee, app.session_state["cq_history"], dataset["events"], dataset["as_of_date"])
        self.assertEqual(app.session_state["cq_skills"]["JUDGE_UPLOAD"], expected_skills)
        expected_recommendations = recommend(
            employee, expected_skills, app.session_state["cq_history"], dataset["events"],
            dataset["role_profiles"], dataset["as_of_date"],
        )
        buttons = [button for button in app.button if button.label == "Complete activity"]
        self.assertEqual([button.key for button in buttons], [
            "complete_JUDGE_UPLOAD_" + item["event"]["event_id"] for item in expected_recommendations
        ])
        self.assertTrue(buttons)
        count = len(app.session_state["cq_history"])
        buttons[0].click().run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.session_state["cq_history"]), count + 1)
        self.assertNotEqual(app.session_state["cq_skills"]["JUDGE_UPLOAD"], expected_skills)
        self.assertEqual(next(metric.value for metric in app.metric if metric.label == "Сотрудников"), "201")

    def test_invalid_upload_leaves_session_unchanged(self):
        app = self.start_app()
        before = copy.deepcopy(app.session_state["cq_skills"])
        count = len(app.session_state["cq_history"])
        self.upload(app, {"employee_id": "BAD", "role": "Unknown", "grade": "Junior", "skills": {}})
        self.assertTrue(app.error)
        self.assertEqual(app.session_state["cq_skills"], before)
        self.assertEqual(len(app.session_state["cq_history"]), count)
        self.assertEqual(len(app.session_state["cq_employees"]), 200)


if __name__ == "__main__":
    unittest.main()
