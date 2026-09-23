"""Small HR overview using the same career and recommendation calculations."""

from collections import Counter

import pandas as pd
import streamlit as st

from recommender import career_progress, no_recommendation_reason, recommend


def render_hr_dashboard(dataset, employees, history, skill_states):
    """Render aggregate statistics for source and uploaded employees alike."""
    st.subheader("HR Dashboard")
    st.caption(
        "Показатели включают загруженных сотрудников и завершения в текущей сессии. "
        "Навыки учитывают завершения после последней оценки."
    )

    events = dataset.get("events", [])
    role_profiles = dataset.get("role_profiles", [])
    skill_names = {
        skill["skill_id"]: skill.get("name") or skill["skill_id"]
        for skill in dataset.get("skills", [])
    }
    event_names = {
        event["event_id"]: event.get("title") or event["event_id"]
        for event in events
    }
    gap_counts = Counter()
    gap_totals = Counter()
    no_recommendations = []
    eligible_count = 0
    eligible_without_recommendations = 0

    for employee in employees:
        employee_id = employee["employee_id"]
        skills = skill_states.get(employee_id, employee.get("skills") or {})
        progress = career_progress(employee, skills, role_profiles)
        if progress.get("next_grade"):
            eligible_count += 1
        for skill_id, gap in progress.get("gaps", {}).items():
            if gap > 0:
                gap_counts[skill_id] += 1
                gap_totals[skill_id] += gap

        recommendations = recommend(
            employee, skills, history, events, role_profiles,
            dataset["as_of_date"], top_n=3,
        )
        if not recommendations:
            if progress.get("next_grade"):
                eligible_without_recommendations += 1
            reason = no_recommendation_reason(
                employee, skills, history, events, role_profiles,
                dataset["as_of_date"],
            )
            no_recommendations.append({
                "Employee ID": employee_id,
                "Сотрудник": employee.get("full_name") or employee_id,
                "Роль": employee.get("role") or "—",
                "Grade": employee.get("grade") or "—",
                "Причина": reason,
            })

    columns = st.columns(3)
    columns[0].metric("Сотрудников", len(employees))
    columns[1].metric("Есть следующий grade", eligible_count)
    columns[2].metric(
        "Без рекомендаций к следующему grade", eligible_without_recommendations,
    )

    st.markdown("### Самые частые skill gaps")
    st.caption(
        "Количество сотрудников с дефицитом навыка для следующего grade "
        "в текущей роли. Lead в этот расчёт не входят."
    )
    gap_rows = [
        {
            "Навык": skill_names.get(skill_id, skill_id),
            "Сотрудников с gap": count,
            "Суммарный дефицит уровней": gap_totals[skill_id],
        }
        for skill_id, count in sorted(
            gap_counts.items(), key=lambda item: (-item[1], item[0]),
        )
    ]
    if gap_rows:
        gap_frame = pd.DataFrame(gap_rows)
        st.bar_chart(
            gap_frame.head(10).set_index("Навык")[["Сотрудников с gap"]],
        )
        st.dataframe(gap_frame, hide_index=True, width="stretch")
    else:
        st.info("Среди сотрудников нет незакрытых требований следующего grade.")

    st.markdown("### Participation по активностям")
    st.caption(
        "Каждая запись истории — одно участие или назначение. "
        "Повторные участия считаются отдельно; сотрудники — уникально."
    )
    if history:
        statuses = [
            "completed", "in_progress", "dropped", "no_show", "declined", "overdue",
        ]
        status_counts = {}
        participants = {}
        for record in history:
            event_id = record.get("event_id")
            if not event_id:
                continue
            status_counts.setdefault(event_id, Counter())[record.get("status", "")] += 1
            if record.get("employee_id"):
                participants.setdefault(event_id, set()).add(record["employee_id"])
        participation_rows = []
        for event_id, counts in sorted(
            status_counts.items(), key=lambda item: (-sum(item[1].values()), item[0]),
        ):
            row = {
                "Event ID": event_id,
                "Активность": event_names.get(event_id, event_id),
                "Участий / назначений": sum(counts.values()),
                "Сотрудников": len(participants.get(event_id, set())),
            }
            row.update({status: counts[status] for status in statuses})
            participation_rows.append(row)
        if participation_rows:
            st.dataframe(
                pd.DataFrame(participation_rows), hide_index=True,
                width="stretch",
            )
        else:
            st.info("В истории нет записей об активностях.")
    else:
        st.info("История участия пока пуста.")

    st.markdown("### Сотрудники без рекомендаций")
    st.caption(
        "Lead уже находятся на последней ступени. Отсутствие следующего grade "
        "или закрытые требования не являются ошибкой подбора."
    )
    if no_recommendations:
        st.dataframe(
            pd.DataFrame(no_recommendations), hide_index=True,
            width="stretch",
        )
    else:
        st.success("Для каждого сотрудника найдены подходящие активности.")
