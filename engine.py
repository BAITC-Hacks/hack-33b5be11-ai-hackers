"""Explainable career recommendations with a history-trained participation model."""
import math
from collections import Counter

GRADES = ['Junior', 'Middle', 'Senior', 'Lead']
NEGATIVE = {'no_show', 'dropped', 'declined'}

class Engine:
    def __init__(self, employees, events, skills, history, today='2026-10-01'):
        self.employees = {e['employee_id']: e for e in employees}
        self.events = {e['event_id']: e for e in events}
        self.skills = {s['skill_id']: s for s in skills['skills']}
        self.profiles = {(p['role'], p['grade']): p for p in skills['role_profiles']}
        self.history = history
        self.today = today
        # Beta-binomial empirical participation estimates; only resolved voluntary records.
        self.counts = {}
        for r in history:
            event = self.events.get(r['event_id'])
            if not event or event['mandatory'] or r['status'] not in NEGATIVE | {'completed'} or r['date'] > today:
                continue
            for key in [('type', event['type']), ('format', event['format']), ('person', r['employee_id'], event['type'])]:
                counts = self.counts.setdefault(key, [0, 0])
                counts[0] += r['status'] == 'completed'; counts[1] += 1

    def participation(self, employee, event):
        probabilities = []
        for key in [('type', event['type']), ('format', event['format']), ('person', employee['employee_id'], event['type'])]:
            good, total = self.counts.get(key, [0, 0])
            probabilities.append((good + 2) / (total + 4))
        return round(.25 * probabilities[0] + .25 * probabilities[1] + .5 * probabilities[2], 3)

    def records(self, eid):
        return sorted([r for r in self.history if r['employee_id'] == eid and r['date'] <= self.today], key=lambda r: (r['date'], r['record_id']))

    @staticmethod
    def apply(levels, event):
        levels = dict(levels)
        for d in event['develops_skills']:
            old = levels.get(d['skill_id'], 0)
            levels[d['skill_id']] = max(old, min(5, d['max_level'], old + d['gain']))
        return levels

    def levels(self, employee):
        result = dict(employee['skills'])
        review = employee.get('last_review_date')
        # With no assessment date there is no safe replay boundary. Keep the
        # uploaded assessment as-is; new demo completions are applied directly
        # by the API in that case.
        if not review:
            return result
        seen = set()
        for r in self.records(employee['employee_id']):
            if r['status'] == 'completed' and r['event_id'] in self.events:
                if r['event_id'] in seen and r['event_id'] != 'EV_036': continue
                seen.add(r['event_id'])
                if r['date'] > review:
                    result = self.apply(result, self.events[r['event_id']])
        return result

    @staticmethod
    def next_grade(employee):
        grade = employee.get('grade')
        if grade not in GRADES or grade == GRADES[-1]:
            return None
        return GRADES[GRADES.index(grade) + 1]

    def target(self, employee):
        goal = employee.get('career_goal')
        if goal: return self.profiles[(goal['target_role'], goal['target_grade'])]
        return self.profiles[(employee['role'], GRADES[min(3, GRADES.index(employee['grade']) + 1)])]

    @staticmethod
    def readiness(levels, target):
        req = target['required_skills']; critical = target['critical_skills']
        total = sum(v * (2 if k in critical else 1) for k, v in req.items())
        earned = sum(min(levels.get(k, 0), v) * (2 if k in critical else 1) for k, v in req.items())
        return round(100 * earned / total, 1) if total else 100

    def candidate(self, employee, event, levels, records):
        reasons = []
        if event['mandatory']: reasons.append('Обязательная активность: вне добровольного развития')
        if employee['role'] not in event['target_roles']: reasons.append('Текущая роль не входит в аудиторию')
        if employee['grade'] not in event['target_grades']: reasons.append('Текущий грейд не входит в аудиторию')
        if any(r['event_id'] == event['event_id'] and r['status'] == 'completed' for r in records) and event['event_id'] != 'EV_036': reasons.append('Уже завершено')
        if event['event_id'] == 'EV_036' and any(r['event_id'] == event['event_id'] and r['date'] == self.today and r['status'] == 'completed' for r in records): reasons.append('Уже завершено сегодня')
        for k, v in event['prerequisites'].items():
            if levels.get(k, 0) < v: reasons.append(f"Допуск: {self.skills[k]['name']} {levels.get(k, 0)}/{v}")
        if event['format'] != 'self_paced' and not any(d >= self.today for d in event['upcoming_sessions']): reasons.append('Нет будущих сессий')
        return reasons

    def profile(self, eid, hours=80):
        e = self.employees[eid]; levels = self.levels(e); target = self.target(e); records = self.records(eid)
        ready = self.readiness(levels, target); candidates = []; excluded = []
        for event in self.events.values():
            reasons = self.candidate(e, event, levels, records)
            if event['duration_hours'] > hours: reasons.append('Превышает выбранный бюджет времени')
            after = self.apply(levels, event)
            effects = {d['skill_id']: d for d in event['develops_skills']}
            changes = []
            for k, effect in effects.items():
                before = levels.get(k, 0); value = after.get(k, before)
                if value <= before: continue
                changes.append({'skill_id': k, 'name': self.skills[k]['name'], 'before': before, 'after': value,
                                'gain': value-before, 'configured_gain': effect['gain'], 'max_level': effect['max_level'],
                                'required': target['required_skills'].get(k, 0), 'critical': k in target['critical_skills']})
            benefit = sum(max(0, min(c['after'], c['required']) - c['before']) * (2 if c['critical'] else 1) for c in changes)
            if benefit <= 0: reasons.append('Не сокращает разрыв до выбранной цели')
            if reasons:
                excluded.append({'event_id': event['event_id'], 'title': event['title'], 'reasons': reasons}); continue
            relevant = [r for r in records if self.events.get(r['event_id'], {}).get('type') == event['type']]
            history_counts = {status: sum(r['status'] == status for r in relevant)
                              for status in ('completed', 'no_show', 'declined', 'dropped')}
            probability = self.participation(e, event)
            history_multiplier = .5 + .5 * probability
            in_progress_multiplier = 1.2 if any(r['event_id'] == event['event_id'] and r['status'] == 'in_progress' for r in records) else 1
            format_multiplier = .8 if e.get('work_format') == 'remote' and event['format'] == 'offline' else 1
            score = benefit * history_multiplier / math.sqrt(max(1, event['duration_hours']))
            score *= in_progress_multiplier * format_multiplier
            candidates.append({**event, 'score': round(score, 4), 'participation_estimate': probability,
                               'history_counts': history_counts, 'changes': changes,
                               'ranking': {'goal_benefit': benefit, 'history_multiplier': round(history_multiplier, 3),
                                           'in_progress_multiplier': in_progress_multiplier,
                                           'format_multiplier': format_multiplier},
                               'readiness_after': self.readiness(after, target),
                               'delta': round(self.readiness(after, target)-ready, 1),
                               'explanation': [
                                   f"Ваш грейд {e['grade']}; цель — {target['role']} / {target['grade']}.",
                                   'Разрывы: ' + '; '.join(
                                       f"{c['name']}: сейчас {c['before']}, нужно {c['required']}, "
                                       f"фактический gain +{c['gain']} (в событии +{c['configured_gain']}, max_level {c['max_level']})"
                                       + (' — критический навык' if c['critical'] else '')
                                       for c in changes if c['required'] > c['before']),
                                   'История того же типа: completed {completed}, no_show {no_show}, '
                                   'declined {declined}, dropped {dropped}. Оценка участия {probability}%; '
                                   'множитель истории {multiplier}.'.format(
                                       probability=round(probability*100), multiplier=round(history_multiplier, 3),
                                       **history_counts),
                                   f"Вклад в цель +{round(self.readiness(after,target)-ready,1)} п.п. за {event['duration_hours']} ч."
                               ]})
        candidates.sort(key=lambda c: (-c['score'], c['event_id']))
        gaps = [{'skill_id': k, 'name': self.skills[k]['name'], 'current': levels.get(k, 0), 'required': v, 'gap': max(0, v-levels.get(k, 0)), 'critical': k in target['critical_skills']} for k, v in target['required_skills'].items()]
        gaps.sort(key=lambda x: (not x['critical'], -x['gap'], x['name']))
        completed = [{**r, 'title': self.events.get(r['event_id'], {}).get('title', r['event_id'])}
                     for r in records if r['status'] == 'completed']
        return {'employee': e, 'levels': levels, 'target': target, 'next_grade': self.next_grade(e),
                'readiness': ready, 'gaps': gaps, 'recommendations': candidates[:3],
                'alternatives': candidates[3:], 'excluded': excluded,
                'history': [{**r, 'title': self.events.get(r['event_id'], {}).get('title', r['event_id'])} for r in records[-20:]][::-1],
                'completed_activities': completed[::-1], 'completed_count': len(completed),
                'as_of_date': self.today,
                'no_step_reason': 'Доступный каталог не закрывает оставшиеся разрывы при выбранном бюджете. Обсудите индивидуальный план с HR.' if not candidates else None}

    def hr(self):
        gaps = Counter(); blocked = []; participation = Counter()
        for eid in self.employees:
            p = self.profile(eid)
            for g in p['gaps']:
                if g['gap']: gaps[g['skill_id']] += 1
            if not p['recommendations']: blocked.append({'employee_id': eid, 'name': p['employee']['full_name'], 'reason': p['no_step_reason']})
        for r in self.history: participation[r['status']] += 1
        return {'employees': len(self.employees), 'gaps': [{'name': self.skills[k]['name'], 'count': n} for k, n in gaps.most_common(12)], 'blocked': blocked, 'participation': dict(participation), 'events': [{'title': e['title'], 'participants': sum(r['event_id']==e['event_id'] for r in self.history), 'completed': sum(r['event_id']==e['event_id'] and r['status']=='completed' for r in self.history)} for e in self.events.values()]}
