"""Career Quest MVP. All demo changes live in Streamlit session_state."""

import copy
from uuid import uuid4

import pandas as pd
import streamlit as st

from data_loader import (
    DataValidationError, STATUS_LABELS, load_dataset,
    parse_employee_upload, parse_history_upload,
)
from hr_dashboard import render_hr_dashboard
from recommender import (
    SCORE_FORMULA, apply_activity, career_progress, effective_skills,
    no_recommendation_reason, recommend,
)


@st.cache_data(show_spinner=False)
def get_dataset():
    return load_dataset()


def initialize_session(dataset):
    if "cq_employees" not in st.session_state:
        st.session_state.cq_employees = copy.deepcopy(dataset["employees"])
        st.session_state.cq_history = copy.deepcopy(dataset["history"])
        st.session_state.cq_skills = {
            employee["employee_id"]: effective_skills(
                employee, dataset["history"], dataset["events"], dataset["as_of_date"],
            )
            for employee in dataset["employees"]
        }


def import_judge_data(employee_content, history_content, dataset):
    """Validate the whole upload before changing any session data.

    An uploaded profile replaces only the matching employee's demo state and
    history. Unrelated employees and their demo completions are preserved.
    """
    employees, warnings = parse_employee_upload(employee_content, dataset)
    history = []
    if history_content is not None:
        history, history_warnings = parse_history_upload(history_content, dataset, employees)
        warnings.extend(history_warnings)
    identifiers = {employee["employee_id"] for employee in employees}
    unrelated = {row["employee_id"] for row in history} - identifiers
    if unrelated:
        raise DataValidationError(
            "CSV должен содержать историю только сотрудников из загруженного JSON. "
            "Лишние employee_id: " + ", ".join(sorted(unrelated))
        )
    if any(row["date"] > dataset["as_of_date"] for row in history):
        raise DataValidationError(
            "История содержит даты после среза " + dataset["as_of_date"] + "."
        )
    remaining_history = [
        row for row in st.session_state.cq_history if row["employee_id"] not in identifiers
    ]
    existing_record_ids = {row["record_id"] for row in remaining_history}
    collisions = existing_record_ids & {row["record_id"] for row in history}
    if collisions:
        raise DataValidationError(
            "record_id уже используются в истории других сотрудников: "
            + ", ".join(sorted(collisions)[:5]) + ". Укажите уникальные record_id."
        )
    states = {
        employee["employee_id"]: effective_skills(
            employee, history, dataset["events"], dataset["as_of_date"],
        ) for employee in employees
    }
    merged = {employee["employee_id"]: employee for employee in st.session_state.cq_employees}
    merged.update({employee["employee_id"]: employee for employee in employees})
    st.session_state.cq_employees = list(merged.values())
    st.session_state.cq_history = remaining_history + history
    st.session_state.cq_skills.update(states)
    st.session_state.employee_selection = employees[0]["employee_id"]
    return employees, warnings


def render_upload(dataset):
    with st.expander("Judge Upload Mode — загрузить сотрудника и историю"):
        st.caption(
            "JSON: объект сотрудника или исходный документ с массивом employees. "
            "CSV: история этих сотрудников с исходными столбцами. История необязательна. "
            "При совпадении employee_id профиль и его история заменяются только в этой сессии."
        )
        with st.form("judge_upload"):
            employee_file = st.file_uploader("Employee JSON", type=["json"], key="judge_employee")
            history_file = st.file_uploader("Activity history CSV", type=["csv"], key="judge_history")
            submitted = st.form_submit_button("Загрузить и рассчитать")
        if submitted:
            if employee_file is None:
                st.error("Выберите Employee JSON. Историю CSV можно добавить вместе с ним.")
            else:
                try:
                    employees, warnings = import_judge_data(
                        employee_file.getvalue(),
                        history_file.getvalue() if history_file is not None else None,
                        dataset,
                    )
                except DataValidationError as exc:
                    st.error(str(exc))
                else:
                    st.session_state.cq_notice = "Загружено сотрудников: {}. Рекомендации пересчитаны.".format(len(employees))
                    st.session_state.cq_warnings = warnings
                    st.rerun()


def complete_activity(employee, event_id, dataset):
    employee_id = employee["employee_id"]
    before = st.session_state.cq_skills[employee_id]
    available = recommend(
        employee, before, st.session_state.cq_history, dataset["events"],
        dataset["role_profiles"], dataset["as_of_date"],
    )
    event = next((item["event"] for item in available if item["event"]["event_id"] == event_id), None)
    if event is None:
        st.warning("Рекомендация уже изменилась. Выберите доступную активность.")
        return
    after = apply_activity(before, event)
    names = {skill["skill_id"]: skill["name"] for skill in dataset["skills"]}
    changes = [
        "{}: {} → {}".format(names.get(skill_id, skill_id), before.get(skill_id, 0), level)
        for skill_id, level in after.items() if level > before.get(skill_id, 0)
    ]
    st.session_state.cq_skills[employee_id] = after
    st.session_state.cq_history.append({
        "record_id": "DEMO_" + uuid4().hex,
        "employee_id": employee_id, "event_id": event_id,
        "date": dataset["as_of_date"], "due_date": None,
        "status": "completed", "completion_pct": 100,
        "score": None, "feedback_rating": None, "assigned_by": "self",
    })
    st.session_state.cq_notice = "Выполнено: {}. {}".format(event["title"], "; ".join(changes))
    st.rerun()


def render_skills(skills, progress, skill_names):
    st.subheader("Skills")
    st.caption("Шкала владения: 0–5. ★ — критический навык следующего grade.")
    requirements = progress["requirements"]
    skill_ids = sorted(
        set(skills) | set(requirements),
        key=lambda skill_id: (
            skill_id not in progress["critical_gaps"],
            skill_id not in progress["gaps"],
            skill_names.get(skill_id, skill_id),
        ),
    )
    columns = st.columns(2)
    for index, skill_id in enumerate(skill_ids):
        current = skills.get(skill_id, 0)
        required = requirements.get(skill_id)
        star = "★ " if skill_id in progress["critical_skills"] else ""
        target = "нужно {}".format(required) if required is not None else "нет требования следующего grade"
        label = "{}{} · {}/5 · {}".format(star, skill_names.get(skill_id, skill_id), current, target)
        with columns[index % 2]:
            st.progress(min(1.0, max(0.0, current / 5)), text=label)


def render_recommendations(employee, skills, dataset, skill_names):
    st.subheader("Recommended next steps")
    st.caption(
        "Баллы 0–100 показывают приоритет активности. Complete activity моделирует "
        "завершение на дату среза; изменения сохраняются только в текущей сессии."
    )
    recommendations = recommend(
        employee, skills, st.session_state.cq_history, dataset["events"],
        dataset["role_profiles"], dataset["as_of_date"],
    )
    if not recommendations:
        st.info(no_recommendation_reason(
            employee, skills, st.session_state.cq_history, dataset["events"],
            dataset["role_profiles"], dataset["as_of_date"],
        ))
    for rank, item in enumerate(recommendations, 1):
        event = item["event"]
        with st.container(border=True):
            st.markdown("#### {}. {}".format(rank, event["title"]))
            st.write("**Балл приоритета: {:.2f}/100**".format(item["score"]))
            sessions = [day for day in event.get("upcoming_sessions", []) if day >= dataset["as_of_date"]]
            availability = "В любое время" if event["format"] == "self_paced" else "Ближайшая сессия: " + min(sessions)
            st.caption("{} · {} · {} ч. · {}".format(
                event["event_id"], event["format"], event.get("duration_hours", "—"), availability,
            ))
            st.write(item["explanation"])
            st.dataframe(pd.DataFrame([
                {
                    "Навык": skill_names.get(impact["skill_id"], impact["skill_id"]),
                    "Сейчас": impact["current"], "Нужно": impact["required"],
                    "После": impact["after"], "Ожидаемый gain": impact["gain"],
                    "max_level": impact["max_level"],
                    "Критический": "Да" if impact["critical"] else "Нет",
                } for impact in item["impacts"]
            ]), hide_index=True, width="stretch")
            with st.expander("Breakdown — почему такой score"):
                st.dataframe(pd.DataFrame([
                    {"Фактор": factor, "Баллы": points, "Расчёт": SCORE_FORMULA[factor]}
                    for factor, points in item["breakdown"].items()
                ]), hide_index=True, width="stretch")
                st.caption(
                    "Похожие активности имеют общие развиваемые навыки. Вес похожести — "
                    "число общих навыков / число всех уникальных навыков двух активностей. "
                    "Полезный gain ограничен дефицитом навыка до следующего grade."
                )
            if st.button("Complete activity", key="complete_{}_{}".format(employee["employee_id"], event["event_id"])):
                complete_activity(employee, event["event_id"], dataset)


def render_employee(dataset):
    render_upload(dataset)
    if st.session_state.get("cq_notice"):
        st.success(st.session_state.pop("cq_notice"))
    for warning in st.session_state.pop("cq_warnings", []):
        st.warning(warning)
    employees = {employee["employee_id"]: employee for employee in st.session_state.cq_employees}
    employee_id = st.selectbox(
        "Employee", list(employees), key="employee_selection",
        format_func=lambda key: "{} · {} · {} · {}".format(
            key, employees[key]["full_name"], employees[key]["role"], employees[key]["grade"],
        ),
    )
    employee = employees[employee_id]
    skills = st.session_state.cq_skills[employee_id]
    progress = career_progress(employee, skills, dataset["role_profiles"])
    skill_names = {skill["skill_id"]: skill["name"] for skill in dataset["skills"]}
    st.subheader(employee["full_name"])
    st.write("**{}** · {} → {}".format(
        employee["role"], employee["grade"], progress["next_grade"] or "последний grade",
    ))
    tenure = employee.get("tenure_months")
    st.caption("Employee ID: {} · {} · Стаж: {} · Последняя оценка: {}".format(
        employee_id, employee.get("department", "—"),
        "{} мес.".format(tenure) if tenure is not None else "не указан",
        employee.get("last_review_date") or "не указана",
    ))
    goal = employee.get("career_goal")
    if goal:
        st.caption("Карьерная цель: {} / {}. Готовность ниже — к следующему grade текущей роли.".format(
            goal["target_role"], goal["target_grade"],
        ))
    if progress["readiness"] is None:
        st.info("Для этого сотрудника не задан следующий grade; career readiness не рассчитывается.")
    else:
        st.metric("Career readiness", "{:.1f}%".format(progress["readiness"]))
        st.progress(progress["readiness"] / 100)
        st.caption("Σ min(текущий уровень, требование) / Σ требований. Учитываются завершения после последней оценки.")
        if progress["critical_gaps"]:
            st.warning("Критические навыки с дефицитом: " + ", ".join(
                skill_names.get(key, key) for key in progress["critical_gaps"]
            ))
        elif progress["gaps"]:
            st.info("Критические навыки закрыты; остаются другие требования следующего grade.")
        else:
            st.success("Все требования по навыкам следующего grade выполнены.")
    render_skills(skills, progress, skill_names)
    render_recommendations(employee, skills, dataset, skill_names)
    with st.expander("История активностей"):
        event_names = {event["event_id"]: event["title"] for event in dataset["events"]}
        rows = sorted(
            [row for row in st.session_state.cq_history if row["employee_id"] == employee_id],
            key=lambda row: row["date"], reverse=True,
        )
        if rows:
            st.dataframe(pd.DataFrame([
                {
                    "Дата": row["date"], "Активность": event_names.get(row["event_id"], row["event_id"]),
                    "Статус": STATUS_LABELS.get(row["status"], row["status"]),
                    "Выполнено, %": row.get("completion_pct"), "Оценка": row.get("score"),
                    "Демо": "Да" if row["record_id"].startswith("DEMO_") else "",
                } for row in rows
            ]), hide_index=True, width="stretch")
        else:
            st.info("История пока пуста.")


def main():
    st.set_page_config(page_title="Career Quest", page_icon="🌱", layout="wide")
    st.title("Career Quest")
    try:
        dataset = get_dataset()
    except DataValidationError as exc:
        st.error(str(exc))
        st.info("Положите исходную папку career_quest_dataset рядом с app.py.")
        st.stop()
    initialize_session(dataset)
    st.caption("Развитие сотрудников · Дата среза: {} · Демо без внешних API".format(dataset["as_of_date"]))
    for warning in dataset["warnings"]:
        st.warning(warning)
    employee_tab, hr_tab = st.tabs(["Employee", "HR Dashboard"])
    with employee_tab:
        render_employee(dataset)
    with hr_tab:
        render_hr_dashboard(dataset, st.session_state.cq_employees, st.session_state.cq_history, st.session_state.cq_skills)


if __name__ == "__main__":
    main()
