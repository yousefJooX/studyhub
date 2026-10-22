"""Exam timetable and an automatic study plan.

The student enters their exams (date, course, how heavy each one is). The plan fills every day from
today until the last exam with study sessions (ordinary tasks with a do-date), so the plan shows up
in Today, the timetable, reminders and the weekly review like any other task:
  * the day before an exam is a final review of that exam,
  * other days go to the exam that is closest and heaviest, spread so no exam is left untouched,
  * rest days get nothing, and an exam day only gets a short review of the next exam.
Re-planning replaces the unfinished plan tasks and keeps the ones already done.
"""
import re
import time
import uuid
from datetime import date, datetime, time as clock_time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from . import app as core

router = APIRouter(prefix='/api/exams', dependencies=[Depends(core.authorized)])
CAIRO = ZoneInfo('Africa/Cairo')
KINDS = {'final': 'فاينال', 'midterm': 'ميدترم', 'quiz': 'كويز', 'practical': 'عملي', 'oral': 'شفوي'}
KINDS_EN = {'final': 'Final', 'midterm': 'Midterm', 'quiz': 'Quiz', 'practical': 'Practical', 'oral': 'Oral'}


def today():
    return datetime.now(CAIRO).date()


class Exam(BaseModel):
    title: str = Field(default='', max_length=200)
    course_id: str = ''
    kind: str = Field(default='final', pattern='^(final|midterm|quiz|practical|oral)$')
    date: str
    time: str = ''
    duration_minutes: int = Field(default=120, ge=0, le=600)
    location: str = Field(default='', max_length=200)
    weight: int = Field(default=3, ge=1, le=5)

    @field_validator('date')
    @classmethod
    def valid_date(cls, value):
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError('Use YYYY-MM-DD')
        date.fromisoformat(value)
        return value

    @field_validator('time')
    @classmethod
    def valid_time(cls, value):
        if value:
            if not re.fullmatch(r'\d{2}:\d{2}', value):
                raise ValueError('Use HH:MM')
            clock_time.fromisoformat(value)
        return value


def label(exam, courses=None, lang='ar'):
    """'Physics · فاينال' — the course name, or the exam's own title when it has no course."""
    courses = courses if courses is not None else {c['id']: c for c in core.rows('courses')}
    name = courses.get(exam.get('course_id'), {}).get('title') or exam.get('title') or ('Exam' if lang == 'en' else 'امتحان')
    kinds = KINDS_EN if lang == 'en' else KINDS
    return name if exam.get('title') and not exam.get('course_id') else f"{name} · {kinds.get(exam.get('kind'), '')}".strip(' ·')


def save(body, exam_id=None):
    core.check_course(body.course_id)
    if not body.course_id and not body.title.strip():
        raise HTTPException(400, 'اختار المادة أو اكتب اسم الامتحان')
    old = core.get('exams', exam_id) if exam_id else {'id': uuid.uuid4().hex, 'created': time.time()}
    return core.put('exams', old | body.model_dump() | {'title': body.title.strip(), 'updated': time.time()})


@router.get('')
def list_exams():
    exams = sorted(core.rows('exams'), key=lambda e: (e['date'], e.get('time') or '99:99'))
    courses = {c['id']: c for c in core.rows('courses')}
    return {'exams': [e | {'label': label(e, courses)} for e in exams], 'today': today().isoformat()}


@router.post('')
def add_exam(body: Exam):
    return save(body)


@router.put('/{exam_id}')
def edit_exam(exam_id: str, body: Exam):
    return save(body, exam_id)


class PlanRequest(BaseModel):
    start: str = ''
    minutes_per_day: int = Field(default=180, ge=25, le=720)
    session_minutes: int = Field(default=50, ge=15, le=180)
    rest_days: list[int] = Field(default_factory=list, max_length=7)
    dry_run: bool = False
    lang: str = Field(default='ar', pattern='^(ar|en)$')

    @field_validator('rest_days')
    @classmethod
    def valid_days(cls, value):
        if any(d not in range(7) for d in value):
            raise ValueError('Days are 0 (Sunday) to 6 (Saturday)')
        return sorted(set(value))


def weekday(day):
    """0 = Sunday … 6 = Saturday, like the timetable."""
    return (day.weekday() + 1) % 7


def build_plan(exams, start, minutes_per_day, session_minutes, rest_days):
    """→ [{'date', 'exam_id', 'kind': 'study'|'review'}], one entry per session."""
    exams = [e | {'_day': date.fromisoformat(e['date'])} for e in exams]
    exams = [e for e in exams if e['_day'] > start]
    if not exams:
        return []
    per_day = max(1, minutes_per_day // session_minutes)
    given = {e['id']: 0 for e in exams}
    plan, day, last = [], start, max(e['_day'] for e in exams)
    while day < last:
        ahead = [e for e in exams if e['_day'] > day]
        sessions = per_day
        if weekday(day) in rest_days:
            sessions = 0
        elif any(e['_day'] == day for e in exams):
            sessions = per_day // 2           # exam day: only a short look at what comes next
        tomorrow = [e for e in ahead if e['_day'] == day + timedelta(days=1)]
        for n in range(sessions):
            if tomorrow:                      # the day before an exam belongs to that exam
                exam, kind = tomorrow[n % len(tomorrow)], 'review'
            else:
                exam = max(ahead, key=lambda e: e['weight'] / (e['_day'] - day).days / (1 + given[e['id']]))
                kind = 'study'
            given[exam['id']] += 1
            plan.append({'date': day.isoformat(), 'exam_id': exam['id'], 'kind': kind})
        day += timedelta(days=1)
    return plan


@router.post('/plan')
def make_plan(body: PlanRequest):
    start = date.fromisoformat(body.start) if body.start else today()
    exams = core.rows('exams')
    plan = build_plan(exams, start, body.minutes_per_day, body.session_minutes, body.rest_days)
    if not plan:
        raise HTTPException(400, 'ضيف امتحان واحد على الأقل بعد النهارده')
    courses = {c['id']: c for c in core.rows('courses')}
    by_id = {e['id']: e for e in exams}
    summary = {}
    for item in plan:
        summary.setdefault(item['exam_id'], {'exam_id': item['exam_id'], 'label': label(by_id[item['exam_id']], courses),
                                             'sessions': 0, 'minutes': 0})
        summary[item['exam_id']]['sessions'] += 1
        summary[item['exam_id']]['minutes'] += body.session_minutes
    result = {'sessions': len(plan), 'days': len({p['date'] for p in plan}), 'by_exam': list(summary.values()),
              'first': plan[0]['date'], 'last': plan[-1]['date']}
    if body.dry_run:
        return result | {'preview': plan[:60]}
    removed = 0
    for task in core.rows('tasks'):  # replace the unfinished part of an older plan
        if task.get('plan') and task.get('status') != 'done' and task.get('planned_date', '') >= start.isoformat():
            with core.database() as db:
                db.execute('DELETE FROM tasks WHERE id=?', (task['id'],))
                db.execute('DELETE FROM reminder_deliveries WHERE task_id=?', (task['id'],))
            removed += 1
    for item in plan:
        exam = by_id[item['exam_id']]
        name, en = label(exam, courses, body.lang), body.lang == 'en'
        title = ({'review': 'Final review: ', 'study': 'Study: '} if en else {'review': 'مراجعة نهائية: ', 'study': 'مذاكرة: '})[item['kind']] + name
        when = exam['date'] + ((' ' + exam['time']) if exam.get('time') else '')
        task = core.Task(title=title[:300], course_id=exam.get('course_id', ''), planned_date=item['date'],
                         duration_minutes=body.session_minutes,
                         details=f'From the exam plan · exam on {when}' if en else f'من خطة الامتحانات · الامتحان يوم {when}')
        value = core.save_entry('tasks', task)
        core.put('tasks', value | {'plan': {'exam_id': exam['id'], 'kind': item['kind']}})
    core.set_setting('exam_plan', body.model_dump(exclude={'dry_run', 'start', 'lang'}) | {'made': time.time()})
    return result | {'created': len(plan), 'removed': removed}


@router.delete('/plan')
def clear_plan():
    """Remove every unfinished plan task (finished ones stay, they count in the weekly review)."""
    removed = 0
    for task in core.rows('tasks'):
        if task.get('plan') and task.get('status') != 'done':
            with core.database() as db:
                db.execute('DELETE FROM tasks WHERE id=?', (task['id'],))
                db.execute('DELETE FROM reminder_deliveries WHERE task_id=?', (task['id'],))
            removed += 1
    return {'removed': removed}


@router.get('/plan/settings')
def plan_settings():
    return core.setting('exam_plan', {'minutes_per_day': 180, 'session_minutes': 50, 'rest_days': []})


# After /plan so DELETE /plan is not taken for an exam id.
@router.delete('/{exam_id}')
def delete_exam(exam_id: str):
    core.get('exams', exam_id)
    with core.database() as db:
        db.execute('DELETE FROM exams WHERE id=?', (exam_id,))
    return {'ok': True}
