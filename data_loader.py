"""Read the original Career Quest schema and validate judge uploads.

No writes or skill updates happen here. Returned objects can safely be kept in
Streamlit session_state without changing the source dataset.
"""

import copy
import csv
import io
import json
from datetime import date
from pathlib import Path


class DataValidationError(ValueError):
    """A readable input error that the UI can display without a traceback."""


STATUS_LABELS = {
    "completed": "Завершено",
    "in_progress": "В процессе",
    "dropped": "Прекращено",
    "no_show": "Пропуск",
    "declined": "Отказ",
    "overdue": "Просрочено",
}
GRADES = ("Junior", "Middle", "Senior", "Lead")
HISTORY_COLUMNS = (
    "record_id", "employee_id", "event_id", "date", "due_date", "status",
    "completion_pct", "score", "feedback_rating", "assigned_by",
)


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise DataValidationError("{}: ожидается непустая строка.".format(field))
    return value.strip()


def _date(value, field, optional=False):
    if optional and value in (None, ""):
        return None
    try:
        if not isinstance(value, str) or len(value) != 10:
            raise ValueError
        return date.fromisoformat(value).isoformat()
    except (ValueError, TypeError):
        raise DataValidationError("{}: ожидается дата YYYY-MM-DD.".format(field))


def _integer(value, field, minimum, maximum=None, optional=False):
    if optional and value in (None, ""):
        return None
    try:
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ValueError
        number = int(value)
        if number < minimum or (maximum is not None and number > maximum):
            raise ValueError
        return number
    except (ValueError, TypeError):
        bounds = "{}–{}".format(minimum, maximum) if maximum is not None else "≥ {}".format(minimum)
        raise DataValidationError("{}: ожидается целое число {}.".format(field, bounds))


def _json(content, filename):
    try:
        return json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DataValidationError("{}: некорректный JSON в UTF-8 ({})".format(filename, exc))


def _array(document, key, filename):
    if not isinstance(document, dict) or not isinstance(document.get(key), list):
        raise DataValidationError("{}: ожидается объект с массивом '{}'.".format(filename, key))
    return document[key]


def _unique_objects(rows, id_field, label):
    seen = set()
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise DataValidationError("{}: запись {} должна быть объектом.".format(label, index))
        identifier = _text(row.get(id_field), "{}[{}].{}".format(label, index, id_field))
        if identifier in seen:
            raise DataValidationError("{}: повторяется {} '{}' .".format(label, id_field, identifier))
        seen.add(identifier)
    return seen


def _skill_levels(value, known_skills, field):
    if not isinstance(value, dict):
        raise DataValidationError("{}: ожидается объект skill_id → level.".format(field))
    result = {}
    for skill_id, level in value.items():
        if skill_id not in known_skills:
            raise DataValidationError("{}: неизвестный skill_id '{}' .".format(field, skill_id))
        result[skill_id] = _integer(level, "{}.{}".format(field, skill_id), 0, 5)
    return result


def _normalize_employees(rows, dataset):
    if not rows:
        raise DataValidationError("Файл сотрудников содержит пустой список employees.")
    _unique_objects(rows, "employee_id", "employees")
    known_skills = {skill["skill_id"] for skill in dataset["skills"]}
    profiles = {(profile["role"], profile["grade"]) for profile in dataset["role_profiles"]}
    result, warnings = [], []
    for original in rows:
        employee = copy.deepcopy(original)
        employee_id = _text(employee.get("employee_id"), "employee_id")
        employee["employee_id"] = employee_id
        employee["role"] = _text(employee.get("role"), "{}.role".format(employee_id))
        employee["grade"] = _text(employee.get("grade"), "{}.grade".format(employee_id))
        if (employee["role"], employee["grade"]) not in profiles:
            raise DataValidationError("{}: роль '{}' и grade '{}' отсутствуют в role_profiles.".format(
                employee_id, employee["role"], employee["grade"]))
        employee["skills"] = _skill_levels(employee.get("skills"), known_skills, employee_id + ".skills")
        employee["full_name"] = employee.get("full_name") or employee_id
        employee["department"] = employee.get("department") or "Не указан"
        employee["manager_id"] = employee.get("manager_id") or None
        employee["work_format"] = employee.get("work_format") or "Не указан"
        employee["preferred_language"] = employee.get("preferred_language") or "ru"
        for field in ("full_name", "department", "work_format", "preferred_language"):
            if not isinstance(employee[field], str):
                warnings.append("{}: некорректное необязательное поле {} заменено на текст.".format(employee_id, field))
                employee[field] = str(employee[field])
        for field in ("hire_date", "last_review_date"):
            try:
                employee[field] = _date(employee.get(field), employee_id + "." + field, optional=True)
            except DataValidationError:
                warnings.append("{}: некорректная {}; поле пропущено.".format(employee_id, field))
                employee[field] = None
        if employee["last_review_date"] is None:
            warnings.append("{}: нет last_review_date — исторические завершения не начисляются повторно.".format(employee_id))
        try:
            employee["tenure_months"] = _integer(employee.get("tenure_months"), employee_id + ".tenure_months", 0, optional=True)
        except DataValidationError:
            warnings.append("{}: некорректный tenure_months; поле пропущено.".format(employee_id))
            employee["tenure_months"] = None
        if employee["tenure_months"] is None and employee["hire_date"]:
            hired = date.fromisoformat(employee["hire_date"])
            as_of = date.fromisoformat(dataset["as_of_date"])
            employee["tenure_months"] = max(0, (as_of.year - hired.year) * 12 + as_of.month - hired.month - (as_of.day < hired.day))
        goal = employee.get("career_goal")
        if goal is not None and (not isinstance(goal, dict) or
                not isinstance(goal.get("target_role"), str) or
                not isinstance(goal.get("target_grade"), str) or
                (goal.get("target_role"), goal.get("target_grade")) not in profiles):
            warnings.append("{}: некорректная career_goal пропущена.".format(employee_id))
            goal = None
        employee["career_goal"] = goal
        result.append(employee)
    return result, warnings


def parse_employee_upload(content, dataset):
    """Accept an original employees document, one employee, or an employee list."""
    document = _json(content, "Employee JSON")
    if isinstance(document, dict) and "employees" in document:
        rows = _array(document, "employees", "Employee JSON")
    elif isinstance(document, dict):
        rows = [document]
    elif isinstance(document, list):
        rows = document
    else:
        raise DataValidationError("Employee JSON: ожидается сотрудник или массив employees.")
    return _normalize_employees(rows, dataset)


def parse_history_upload(content, dataset, employees):
    """Read original CSV records; extra employees participate in reference checks."""
    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise DataValidationError("Activity history CSV должен быть в кодировке UTF-8.")
    reader = csv.DictReader(io.StringIO(decoded, newline=""), strict=True)
    required = {"record_id", "employee_id", "event_id", "date", "status"}
    try:
        headers = reader.fieldnames or []
        if len(headers) != len(set(headers)):
            raise DataValidationError("Activity history CSV: повторяющиеся названия столбцов.")
        missing = required - set(headers)
        if missing:
            raise DataValidationError("Activity history CSV: отсутствуют столбцы {}. Разделитель — запятая.".format(
                ", ".join(sorted(missing))))
        known_employees = {employee["employee_id"] for employee in dataset.get("employees", []) + employees}
        known_events = {event["event_id"] for event in dataset["events"]}
        warnings, rows, seen = [], [], set()
        optional_missing = set(HISTORY_COLUMNS) - required - set(headers)
        if optional_missing:
            warnings.append("CSV: отсутствующие необязательные столбцы заполнены пустыми значениями: {}.".format(
                ", ".join(sorted(optional_missing))))
        for row in reader:
            label = "CSV, строка {}".format(reader.line_num)
            if None in row:
                raise DataValidationError("{}: число значений превышает число столбцов.".format(label))
            record = {key: (row.get(key) or "").strip() for key in HISTORY_COLUMNS}
            for field in required:
                record[field] = _text(record[field], label + "." + field)
            if record["record_id"] in seen:
                raise DataValidationError("{}: повторяется record_id '{}' .".format(label, record["record_id"]))
            seen.add(record["record_id"])
            if record["employee_id"] not in known_employees:
                raise DataValidationError("{}: неизвестный employee_id '{}' .".format(label, record["employee_id"]))
            if record["event_id"] not in known_events:
                raise DataValidationError("{}: неизвестный event_id '{}' .".format(label, record["event_id"]))
            if record["status"] not in STATUS_LABELS:
                raise DataValidationError("{}: неизвестный status '{}' .".format(label, record["status"]))
            record["date"] = _date(record["date"], label + ".date")
            record["due_date"] = _date(record["due_date"], label + ".due_date", optional=True)
            record["completion_pct"] = _integer(record["completion_pct"], label + ".completion_pct", 0, 100, optional=True)
            record["score"] = _integer(record["score"], label + ".score", 0, 100, optional=True)
            record["feedback_rating"] = _integer(record["feedback_rating"], label + ".feedback_rating", 1, 5, optional=True)
            record["assigned_by"] = record["assigned_by"] or None
            if record["assigned_by"] not in (None, "self", "manager", "hr"):
                raise DataValidationError("{}: assigned_by должен быть self, manager или hr.".format(label))
            rows.append(record)
        rows.sort(key=lambda row: (row["date"], row["employee_id"], row["event_id"], row["record_id"]))
        return rows, warnings
    except csv.Error as exc:
        raise DataValidationError("Activity history CSV: ошибка чтения ({})".format(exc))


def load_dataset(data_dir=None):
    """Load and validate the four real dataset files, without touching originals."""
    directory = Path(data_dir) if data_dir is not None else Path(__file__).resolve().parent / "career_quest_dataset"
    documents = {}
    try:
        for filename in ("employees.json", "events.json", "skills.json"):
            documents[filename] = _json((directory / filename).read_bytes(), filename)
        history_content = (directory / "activity_history.csv").read_bytes()
    except OSError as exc:
        raise DataValidationError("Не удалось прочитать датасет из '{}': {}".format(directory, exc))
    skills_doc = documents["skills.json"]
    skills = _array(skills_doc, "skills", "skills.json")
    profiles = _array(skills_doc, "role_profiles", "skills.json")
    events = _array(documents["events.json"], "events", "events.json")
    employee_rows = _array(documents["employees.json"], "employees", "employees.json")
    known_skills = _unique_objects(skills, "skill_id", "skills")
    _unique_objects(events, "event_id", "events")
    if not known_skills or not profiles:
        raise DataValidationError("skills.json: каталоги skills и role_profiles не должны быть пустыми.")
    snapshots = set()
    for filename, document in documents.items():
        metadata = document.get("meta")
        if isinstance(metadata, dict) and metadata.get("as_of_date"):
            snapshots.add(_date(metadata["as_of_date"], filename + ".meta.as_of_date"))
    if len(snapshots) != 1:
        raise DataValidationError("В meta датасета должна быть одна согласованная дата as_of_date.")
    profile_keys = set()
    for profile in profiles:
        if not isinstance(profile, dict):
            raise DataValidationError("role_profiles: каждая запись должна быть объектом.")
        role = _text(profile.get("role"), "role_profiles.role")
        grade = _text(profile.get("grade"), "role_profiles.grade")
        if grade not in GRADES or (role, grade) in profile_keys:
            raise DataValidationError("role_profiles: недопустимый или повторный профиль {} / {}.".format(role, grade))
        profile_keys.add((role, grade))
        profile["required_skills"] = _skill_levels(profile.get("required_skills"), known_skills, role + ".required_skills")
        critical = profile.get("critical_skills") or []
        if not isinstance(critical, list) or any(not isinstance(skill, str) or skill not in profile["required_skills"] for skill in critical):
            raise DataValidationError("{}: critical_skills должны ссылаться на required_skills.".format(role))
        profile["critical_skills"] = critical
    roles = {role for role, _ in profile_keys}
    for skill in skills:
        skill["name"] = skill.get("name") or skill["skill_id"]
        skill["description"] = skill.get("description") or ""
    for event in events:
        identifier = event["event_id"]
        event["title"] = event.get("title") or identifier
        event["description"] = event.get("description") or ""
        if not isinstance(event.get("mandatory"), bool):
            raise DataValidationError("{}.mandatory: ожидается true или false.".format(identifier))
        for field, allowed in (("target_roles", roles), ("target_grades", set(GRADES))):
            values = event.get(field)
            if not isinstance(values, list) or any(not isinstance(value, str) or value not in allowed for value in values):
                raise DataValidationError("{}.{}: некорректные роли или grade.".format(identifier, field))
        if event.get("format") not in ("online", "offline", "self_paced"):
            raise DataValidationError("{}.format: неизвестный формат.".format(identifier))
        event["prerequisites"] = _skill_levels(event.get("prerequisites") or {}, known_skills, identifier + ".prerequisites")
        developments = event.get("develops_skills")
        if not isinstance(developments, list):
            raise DataValidationError("{}.develops_skills: ожидается массив.".format(identifier))
        _unique_objects(developments, "skill_id", identifier + ".develops_skills")
        for development in developments:
            if development["skill_id"] not in known_skills:
                raise DataValidationError("{}: неизвестный развиваемый skill_id.".format(identifier))
            for field in ("gain", "max_level"):
                development[field] = _integer(development.get(field), identifier + "." + field, 0, 5)
        sessions = event.get("upcoming_sessions") or []
        if not isinstance(sessions, list):
            raise DataValidationError("{}.upcoming_sessions: ожидается массив дат.".format(identifier))
        event["upcoming_sessions"] = [_date(value, identifier + ".upcoming_sessions") for value in sessions]
    dataset = {
        "employees": [], "events": events, "skills": skills, "role_profiles": profiles,
        "proficiency_scale": skills_doc.get("proficiency_scale") or {},
        "as_of_date": next(iter(snapshots)), "history": [], "warnings": [],
    }
    dataset["employees"], warnings = _normalize_employees(employee_rows, dataset)
    dataset["warnings"].extend(warnings)
    known_employees = {employee["employee_id"] for employee in dataset["employees"]}
    for employee in dataset["employees"]:
        manager = employee.get("manager_id")
        if manager is not None and (not isinstance(manager, str) or manager not in known_employees):
            dataset["warnings"].append("{}: manager_id не найден в employees; поле пропущено.".format(employee["employee_id"]))
            employee["manager_id"] = None
    dataset["history"], warnings = parse_history_upload(history_content, dataset, [])
    dataset["warnings"].extend(warnings)
    return dataset
