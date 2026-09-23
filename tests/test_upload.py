"""Judge uploads use the real schema and the same recommendation engine."""

import copy
import csv
import hashlib
import io
import json
from pathlib import Path
import unittest

from data_loader import (
    DataValidationError, HISTORY_COLUMNS, load_dataset,
    parse_employee_upload, parse_history_upload,
)
from recommender import effective_skills, recommend


DATA = Path(__file__).resolve().parents[1] / "career_quest_dataset"


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def history_bytes(rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=HISTORY_COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8-sig")


class JudgeUploadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = load_dataset(DATA)

    def minimal_employee(self):
        return {"employee_id": "JUDGE_01", "role": "Backend Engineer",
                "grade": "Middle", "skills": {"SK_PUBLIC_SPEAKING": 0}}

    def test_document_single_and_list_accept_minimal_optional_fields(self):
        employee = self.minimal_employee()
        for payload in (employee, [employee], {"employees": [employee]}):
            with self.subTest(payload_type=type(payload).__name__):
                rows, warnings = parse_employee_upload(json_bytes(payload), self.dataset)
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["employee_id"], "JUDGE_01")
                self.assertEqual(rows[0]["full_name"], "JUDGE_01")
                self.assertIsNone(rows[0]["last_review_date"])
                self.assertTrue(any("last_review_date" in warning for warning in warnings))
                self.assertEqual(effective_skills(rows[0], [], self.dataset["events"],
                                                 self.dataset["as_of_date"]), employee["skills"])

    def test_invalid_employee_payloads_report_validation_errors(self):
        employee = self.minimal_employee()
        invalid = [b"{broken", b"\xff", json_bytes([]), json_bytes([employee, employee])]
        for update in ({"employee_id": None}, {"role": "Unknown role"},
                       {"grade": "Principal"}, {"skills": {"UNKNOWN_SKILL": 1}},
                       {"skills": {"SK_PUBLIC_SPEAKING": 6}}):
            invalid.append(json_bytes(dict(employee, **update)))
        for content in invalid:
            with self.subTest(content=content[:80]):
                with self.assertRaises(DataValidationError):
                    parse_employee_upload(content, self.dataset)

    def test_uploaded_history_replays_after_review_through_same_engine(self):
        employee = self.minimal_employee()
        employee["last_review_date"] = "2026-09-01"
        uploaded, _ = parse_employee_upload(json_bytes(employee), self.dataset)
        rows = [
            {"record_id": "JUDGE_R1", "employee_id": "JUDGE_01", "event_id": "EV_036",
             "date": "2026-08-31", "status": "completed", "completion_pct": 100},
            {"record_id": "JUDGE_R2", "employee_id": "JUDGE_01", "event_id": "EV_036",
             "date": "2026-09-15", "status": "completed", "completion_pct": 100},
        ]
        history, _ = parse_history_upload(history_bytes(rows), self.dataset, uploaded)
        self.assertIsNone(history[0]["score"])
        self.assertEqual(history[0]["completion_pct"], 100)
        skills = effective_skills(uploaded[0], history, self.dataset["events"], self.dataset["as_of_date"])
        self.assertEqual(skills["SK_PUBLIC_SPEAKING"], 1)
        self.assertEqual(uploaded[0]["skills"]["SK_PUBLIC_SPEAKING"], 0)
        expected = effective_skills(employee, rows, self.dataset["events"], self.dataset["as_of_date"])
        self.assertEqual(skills, expected)
        recommendations = recommend(uploaded[0], skills, history, self.dataset["events"],
                                    self.dataset["role_profiles"], self.dataset["as_of_date"])
        self.assertTrue(recommendations)
        self.assertEqual(recommendations, recommend(employee, expected, rows, self.dataset["events"],
                                                   self.dataset["role_profiles"], self.dataset["as_of_date"]))

    def test_invalid_history_references_dates_and_status_fail_clearly(self):
        employee = self.minimal_employee()
        row = {"record_id": "JUDGE_R", "employee_id": "JUDGE_01", "event_id": "EV_036",
               "date": "2026-09-15", "status": "no_show", "completion_pct": 0}
        invalid = [b"employee_id;event_id;status\n", b'record_id,employee_id,event_id,date,status\n"unterminated']
        for update in ({"employee_id": "UNKNOWN_EMPLOYEE"}, {"event_id": "UNKNOWN_EVENT"},
                       {"date": "2026-02-30"}, {"status": "cancelled"}, {"score": 101}):
            invalid.append(history_bytes([dict(row, **update)]))
        invalid.append(history_bytes([row, row]))
        for content in invalid:
            with self.subTest(content=content[-80:]):
                with self.assertRaises(DataValidationError):
                    parse_history_upload(content, self.dataset, [employee])
        history, warnings = parse_history_upload(
            b"record_id,employee_id,event_id,date,status\nJUDGE_R,JUDGE_01,EV_036,2026-09-15,no_show\n",
            self.dataset, [employee],
        )
        self.assertEqual(history[0]["employee_id"], "JUDGE_01")
        self.assertIsNone(history[0]["feedback_rating"])
        self.assertTrue(warnings)

    def test_original_files_and_loaded_data_are_unchanged_by_uploads(self):
        def hashes():
            return {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in DATA.iterdir() if path.is_file()}

        before_hashes = hashes()
        before_dataset = copy.deepcopy(self.dataset)
        employees, _ = parse_employee_upload((DATA / "employees.json").read_bytes(), self.dataset)
        history, _ = parse_history_upload((DATA / "activity_history.csv").read_bytes(), self.dataset, employees)
        self.assertEqual(len(employees), 200)
        self.assertEqual(len(history), 2743)
        employees[0]["skills"]["SK_PYTHON"] = 0
        history[0]["status"] = "no_show"
        self.assertEqual(self.dataset, before_dataset)
        self.assertEqual(hashes(), before_hashes)


if __name__ == "__main__":
    unittest.main()
