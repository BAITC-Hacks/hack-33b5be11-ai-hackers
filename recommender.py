"""Deterministic Career Quest recommendations, using the dataset's rules.

The score is a priority score, not a probability. All contributions are points:
* skill_gap: 30 * mean(gap / required level) for skills the event can improve.
* next_grade_relevance: 25 * critical useful gain / total useful gain.
* activity_match: 35 * useful gain / sum of all next-grade gaps.
* participation_history: min(10, 2 * weighted similar completions).
* skip_penalty: -min(30, 6 * weighted no_show + 6 * weighted declined
  + 4 * weighted dropped).
Similarity is Jaccard overlap of the two events' developed skill sets.
Useful gain is min(actual gain after max_level, gap). Final score is the sum,
clamped to [0, 100]. Event ID resolves ties. No randomness or external API.
"""

from datetime import date
from math import isfinite


GRADE_ORDER = ("Junior", "Middle", "Senior", "Lead")
# README.ru.md documents this exception; events.json has no repeatable field.
REPEATABLE_EVENT_IDS = frozenset({"EV_036"})
SCORE_FORMULA = {
    "skill_gap": "30 × средний относительный разрыв в улучшаемых навыках",
    "next_grade_relevance": "25 × доля полезного gain в критических навыках",
    "activity_match": "35 × полезный gain / сумма всех разрывов следующего grade",
    "participation_history": "min(10, 2 × взвешенные завершения похожих активностей)",
    "skip_penalty": "−min(30, 6 × no_show + 6 × declined + 4 × dropped), с учётом похожести",
    "final_score": "Сумма факторов, ограниченная от 0 до 100; это балл приоритета",
}


def _number(value, default=0):
    try:
        result = float(value)
        return result if isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _level(value):
    result = max(0, min(5, _number(value)))
    return int(result) if result == int(result) else result


def _date(value):
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _history(employee, history, as_of_date):
    """Filter an employee's records; ignore future and duplicated records."""
    cutoff = _date(as_of_date)
    employee_id = employee.get("employee_id")
    rows, seen = [], set()
    for row in history or []:
        if row.get("employee_id") != employee_id:
            continue
        when = _date(row.get("date"))
        if when is None or cutoff is None or when > cutoff:
            continue
        identity = row.get("record_id") or (
            row.get("employee_id"), row.get("event_id"), row.get("date"), row.get("status")
        )
        if identity in seen:
            continue
        seen.add(identity)
        rows.append(row)
    return sorted(rows, key=lambda row: (row["date"], str(row.get("record_id", ""))))


def apply_activity(skills, event):
    """Return new levels; never mutate inputs or lower skills above an event cap."""
    updated = {skill_id: _level(level) for skill_id, level in (skills or {}).items()}
    for effect in event.get("develops_skills") or []:
        skill_id = effect.get("skill_id")
        if not skill_id:
            continue
        current = updated.get(skill_id, 0)
        # Missing effect parameters must not invent a gain.
        cap = _level(effect.get("max_level", current))
        gain = max(0, _number(effect.get("gain")))
        updated[skill_id] = _level(current + max(0, min(gain, cap - current)))
    return updated


def effective_skills(employee, history, events, as_of_date):
    """Replay completed events after the last review, chronologically, once.

    Call this with the original assessed profile. The UI stores its result in
    session_state and directly applies new demo completions to that state.
    With no valid review date, assessed levels are retained (no guessed replay).
    """
    skills = {key: _level(value) for key, value in (employee.get("skills") or {}).items()}
    review = _date(employee.get("last_review_date"))
    if review is None:
        return skills
    events_by_id = {event.get("event_id"): event for event in events or []}
    completed_events = set()
    for row in _history(employee, history, as_of_date):
        if row.get("status") != "completed":
            continue
        event_id = row.get("event_id")
        if event_id in completed_events and event_id not in REPEATABLE_EVENT_IDS:
            continue
        completed_events.add(event_id)
        if _date(row["date"]) > review and event_id in events_by_id:
            skills = apply_activity(skills, events_by_id[event_id])
    return skills


def career_progress(employee, skills, role_profiles):
    """Readiness for the next grade of the current role; no promotion inference."""
    grade = employee.get("grade")
    next_grade = None
    if grade in GRADE_ORDER and grade != GRADE_ORDER[-1]:
        next_grade = GRADE_ORDER[GRADE_ORDER.index(grade) + 1]
    profile = next(
        (row for row in role_profiles or []
         if row.get("role") == employee.get("role") and row.get("grade") == next_grade),
        None,
    ) if next_grade else None
    requirements = {
        skill_id: _level(level)
        for skill_id, level in ((profile or {}).get("required_skills") or {}).items()
    }
    critical = list((profile or {}).get("critical_skills") or [])
    gaps = {
        skill_id: required - _level((skills or {}).get(skill_id, 0))
        for skill_id, required in requirements.items()
        if required > _level((skills or {}).get(skill_id, 0))
    }
    total = sum(requirements.values())
    readiness = round(100 * (total - sum(gaps.values())) / total, 2) if total else None
    return {
        "next_grade": next_grade,
        "requirements": requirements,
        "critical_skills": critical,
        "gaps": gaps,
        "critical_gaps": {key: value for key, value in gaps.items() if key in critical},
        "readiness": readiness,
        "profile_found": profile is not None,
    }


def _developed_skills(event):
    return {effect["skill_id"] for effect in event.get("develops_skills") or []
            if effect.get("skill_id") and _number(effect.get("gain")) > 0}


def _eligible(event, employee, skills, history, as_of_date):
    if event.get("mandatory", False):
        return False
    if employee.get("role") not in (event.get("target_roles") or []):
        return False
    if employee.get("grade") not in (event.get("target_grades") or []):
        return False
    if any(_level(skills.get(key, 0)) < _number(required)
           for key, required in (event.get("prerequisites") or {}).items()):
        return False
    event_id = event.get("event_id")
    event_history = [row for row in history if row.get("event_id") == event_id]
    if event_id not in REPEATABLE_EVENT_IDS and any(
        row.get("status") == "completed" for row in event_history
    ):
        return False
    # A later terminal record releases a previously in-progress activity.
    if event_history and event_history[-1].get("status") == "in_progress":
        return False
    if event.get("format") != "self_paced":
        cutoff = _date(as_of_date)
        if cutoff is None or not any(
            _date(session) is not None and _date(session) >= cutoff
            for session in event.get("upcoming_sessions") or []
        ):
            return False
    return True


def recommend(employee, skills, history, events, role_profiles, as_of_date, top_n=3):
    """Return up to top_n eligible events with score, reasons and useful impacts."""
    progress = career_progress(employee, skills, role_profiles)
    gaps = progress["gaps"]
    if not gaps or top_n <= 0:
        return []
    employee_history = _history(employee, history, as_of_date)
    events_by_id = {event.get("event_id"): event for event in events or []}
    developed_by_id = {key: _developed_skills(event) for key, event in events_by_id.items()}
    recommendations = []
    for event in events or []:
        if not _eligible(event, employee, skills, employee_history, as_of_date):
            continue
        after = apply_activity(skills, event)
        effects = {effect.get("skill_id"): effect for effect in event.get("develops_skills") or []}
        impacts = []
        for skill_id, gap in gaps.items():
            current = _level(skills.get(skill_id, 0))
            actual_gain = after.get(skill_id, current) - current
            if actual_gain <= 0:
                continue
            effect = effects[skill_id]
            impacts.append({
                "skill_id": skill_id,
                "current": current,
                "required": progress["requirements"][skill_id],
                "after": after[skill_id],
                "gain": actual_gain,
                "useful_gain": min(gap, actual_gain),
                "configured_gain": effect.get("gain", 0),
                "max_level": effect.get("max_level", current),
                "critical": skill_id in progress["critical_skills"],
            })
        if not impacts:
            continue
        useful_gain = sum(impact["useful_gain"] for impact in impacts)
        critical_gain = sum(impact["useful_gain"] for impact in impacts if impact["critical"])
        candidate_skills = developed_by_id.get(event.get("event_id"), set())
        weighted_history = {status: 0.0 for status in ("completed", "no_show", "declined", "dropped")}
        similar_counts = {status: 0 for status in weighted_history}
        for row in employee_history:
            status = row.get("status")
            if status not in weighted_history:
                continue
            prior_skills = developed_by_id.get(row.get("event_id"), set())
            overlap = candidate_skills & prior_skills
            if overlap:
                similarity = len(overlap) / len(candidate_skills | prior_skills)
                weighted_history[status] += similarity
                similar_counts[status] += 1
        raw = {
            "skill_gap": 30 * sum(gaps[item["skill_id"]] / item["required"] for item in impacts) / len(impacts),
            "next_grade_relevance": 25 * critical_gain / useful_gain,
            "activity_match": 35 * useful_gain / sum(gaps.values()),
            "participation_history": min(10, 2 * weighted_history["completed"]),
            "skip_penalty": -min(30, 6 * weighted_history["no_show"]
                                 + 6 * weighted_history["declined"]
                                 + 4 * weighted_history["dropped"]),
        }
        breakdown = {key: round(value, 2) for key, value in raw.items()}
        score = round(max(0, min(100, sum(breakdown.values()))), 2)
        breakdown["final_score"] = score
        reason = (
            "Подходит для текущих роли и grade, требования к участию выполнены. "
            "Закрывает {:g} из {:g} суммарных уровней разрыва до {}.".format(
                useful_gain, sum(gaps.values()), progress["next_grade"]
            )
        )
        if critical_gain:
            reason += " Из них {:g} — в критических навыках для повышения.".format(critical_gain)
        negative_count = sum(similar_counts[key] for key in ("no_show", "declined", "dropped"))
        if negative_count:
            reason += " Учтены пропуски/отказы/прекращения похожих активностей: {} (штраф {:g}).".format(
                negative_count, abs(breakdown["skip_penalty"])
            )
        if similar_counts["completed"]:
            reason += " Учтены успешные завершения похожих активностей: {} (+{:g}).".format(
                similar_counts["completed"], breakdown["participation_history"]
            )
        recommendations.append({
            "event": event,
            "score": score,
            "breakdown": breakdown,
            "impacts": impacts,
            "explanation": reason,
            "metrics": {
                "useful_gain": useful_gain,
                "critical_gain": critical_gain,
                "total_gap": sum(gaps.values()),
                "similar_history_counts": similar_counts,
                "weighted_history": {key: round(value, 4) for key, value in weighted_history.items()},
            },
        })
    recommendations.sort(key=lambda item: (-item["score"], str(item["event"].get("event_id", ""))))
    return recommendations[:top_n]


def no_recommendation_reason(employee, skills, history, events, role_profiles, as_of_date):
    progress = career_progress(employee, skills, role_profiles)
    if employee.get("grade") == "Lead":
        return "Lead — последний grade в датасете; следующий grade не задан."
    if not progress["profile_found"]:
        return "В каталоге нет требований следующего grade для этой роли."
    if not progress["gaps"]:
        return "Все требования по навыкам следующего grade уже выполнены."
    if recommend(employee, skills, history, events, role_profiles, as_of_date, top_n=1):
        return ""
    return (
        "Есть skill gaps, но в каталоге нет доступной активности, которая их уменьшит "
        "с учётом роли, grade, prerequisites, max_level, расписания и истории участия."
    )
