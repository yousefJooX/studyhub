"""StudyHub MCP server (Model Context Protocol, streamable HTTP, stateless JSON responses).

Lets Claude (claude.ai custom connector, Claude Desktop or Claude Code) read and update
StudyHub with the owner's own Claude subscription. Auth: a long random API token
(`python -m studyhub token`), sent as `Authorization: Bearer <token>` or embedded in the
connector URL `/mcp/<token>` for clients that cannot send headers.
"""
import hashlib
import hmac
import json
import logging
import os
import re
import time
from datetime import date, datetime, timedelta, time as clock_time
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from . import app as core

CAIRO = ZoneInfo('Africa/Cairo')
PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
SERVER_INFO = {'name': 'studyhub', 'title': 'StudyHub — مساحتي الدراسية', 'version': '1.0.0'}
INSTRUCTIONS = (
    "StudyHub is the user's personal study workspace: university courses, tasks with deadlines, "
    "a weekly timetable, exams with a study plan, flashcards, college grades, Obsidian lecture notes and uploaded files. "
    "Times are Africa/Cairo. Call get_today first for context. Prefer search_notes + read_note before "
    "answering questions about course material, and cite the note title/path you used. "
    "To build an exam plan, call plan_study with apply=false first, show the user the summary, and apply "
    "only when they agree. To quiz the user, take cards from list_flashcards, ask the front, wait for "
    "their answer, then record it with review_flashcard. When the user sends grades (a list, a grade sheet "
    "or a screenshot), record every row with add_grades, then show what was saved with the course totals. "
    "Ask before deleting or overwriting anything (delete_exam, delete_slot, delete_grade, update_*); "
    "every create_*/update_*/delete_* tool changes real data."
)
router = APIRouter()
DAY_NAMES = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']


# ---------------------------------------------------------------- auth

def token_valid(token):
    expected = os.environ.get('STUDY_API_TOKEN_HASH', '')
    if not expected or not token or len(token) < 32:
        return False
    return hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), expected)


def bearer_token(request: Request):
    header = request.headers.get('authorization', '')
    return header[7:].strip() if header.lower().startswith('bearer ') else ''


class RedactTokens(logging.Filter):
    """Keep the connector URL token out of uvicorn access logs / journald."""
    pattern = re.compile(r'/mcp/[^/?\s"]+')

    def filter(self, record):
        if isinstance(record.args, tuple):
            record.args = tuple(self.pattern.sub('/mcp/***', a) if isinstance(a, str) else a for a in record.args)
        return True


logging.getLogger('uvicorn.access').addFilter(RedactTokens())


# ---------------------------------------------------------------- helpers

def now_cairo():
    return datetime.now(CAIRO)


def course_index():
    return {c['id']: c for c in core.rows('courses')}


def resolve_course(value, courses=None):
    """Accept a course id or (part of) its title; '' means no course."""
    if not value:
        return ''
    courses = courses or course_index()
    if value in courses:
        return value
    needle = value.strip().lower()
    exact = [c for c in courses.values() if c['title'].lower() == needle]
    partial = exact or [c for c in courses.values() if needle in c['title'].lower()]
    if len(partial) == 1:
        return partial[0]['id']
    if not partial:
        raise ValueError(f'No course matches "{value}". Call list_courses to see the exact names.')
    raise ValueError(f'"{value}" matches several courses: ' + ', '.join(c['title'] for c in partial))


def parse_due(value):
    """'2026-10-23' -> end of that day in Cairo; naive datetimes are Cairo time."""
    if not value:
        return ''
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        return datetime.combine(date.fromisoformat(value), clock_time(23, 59), CAIRO).isoformat()
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=CAIRO)
    return moment.isoformat()


def due_dt(task):
    try:
        return datetime.fromisoformat(task['due']) if task.get('due') else None
    except ValueError:
        return None


def task_view(task, courses):
    due = due_dt(task)
    timer = task.get('timer') or {}
    view = {
        'id': task['id'], 'title': task['title'], 'status': task.get('status', 'todo'),
        'priority': task.get('priority', 'normal'),
        'course': courses.get(task.get('course_id', ''), {}).get('title', ''),
        'due': due.astimezone(CAIRO).strftime('%Y-%m-%d %H:%M') if due else '',
        'overdue': bool(due and due < now_cairo() and task.get('status') != 'done'),
        'planned': (task.get('planned_date', '') + ' ' + task.get('planned_time', '')).strip(),
        'duration_minutes': task.get('duration_minutes', 25),
    }
    if task.get('details'):
        view['details'] = task['details'][:500]
    if timer.get('state') == 'running' and timer.get('ends_at', 0) > time.time():
        view['timer'] = f"running, {round((timer['ends_at'] - time.time()) / 60)} min left"
    return view


def slot_view(slot, courses):
    return {'id': slot['id'], 'day': DAY_NAMES[slot['day']], 'start': slot['start'], 'end': slot['end'],
            'title': slot['title'], 'kind': slot.get('kind', 'study'), 'location': slot.get('location', ''),
            'course': courses.get(slot.get('course_id', ''), {}).get('title', '')}


def cairo_weekday(moment):
    return (moment.weekday() + 1) % 7  # Python Monday=0 -> StudyHub Sunday=0


def snippet(text, needle, width=160):
    index = text.lower().find(needle.lower())
    if index < 0:
        return text[:width].replace('\n', ' ')
    start = max(0, index - width // 2)
    return ('…' if start else '') + text[start:start + width].replace('\n', ' ') + '…'


# ---------------------------------------------------------------- tools

def tool_get_today(args):
    courses, now = course_index(), now_cairo()
    today = now.date().isoformat()
    tasks = core.rows('tasks')
    open_tasks = [t for t in tasks if t.get('status') != 'done']
    soon = now + timedelta(days=2)
    focus = [t for t in open_tasks if t.get('planned_date') == today or (due_dt(t) and due_dt(t) <= soon)]
    focus.sort(key=lambda t: (due_dt(t) or datetime.max.replace(tzinfo=CAIRO)))
    slots = sorted((s for s in core.rows('slots') if s['day'] == cairo_weekday(now)), key=lambda s: s['start'])
    return {
        'now': now.strftime('%A %Y-%m-%d %H:%M') + ' (Cairo)',
        'today_and_next_48h': [task_view(t, courses) for t in focus],
        'overdue': [task_view(t, courses) for t in open_tasks if task_view(t, courses)['overdue']],
        'todays_schedule': [slot_view(s, courses) for s in slots],
        'open_task_count': len(open_tasks),
    }


def tool_list_tasks(args):
    courses = course_index()
    status = args.get('status', 'open')
    course_id = resolve_course(args.get('course', ''), courses)
    tasks = core.rows('tasks')
    if status == 'open':
        tasks = [t for t in tasks if t.get('status') != 'done']
    elif status in ('todo', 'doing', 'done'):
        tasks = [t for t in tasks if t.get('status') == status]
    if course_id:
        tasks = [t for t in tasks if t.get('course_id') == course_id]
    if args.get('query'):
        q = args['query'].lower()
        tasks = [t for t in tasks if q in (t['title'] + ' ' + t.get('details', '')).lower()]
    tasks.sort(key=lambda t: (due_dt(t) or datetime.max.replace(tzinfo=CAIRO), -t.get('updated', 0)))
    limit = int(args.get('limit', 50))
    return {'count': len(tasks), 'tasks': [task_view(t, courses) for t in tasks[:limit]]}


def build_task(existing, args, courses):
    fields = {k: existing.get(k) for k in core.Task.model_fields if k in existing}
    for key in ('title', 'status', 'priority', 'details', 'planned_date', 'planned_time', 'duration_minutes'):
        if key in args and args[key] is not None:
            fields[key] = args[key]
    if 'course' in args:
        fields['course_id'] = resolve_course(args['course'], courses)
    if 'due' in args:
        fields['due'] = parse_due(args['due'])
    if 'remind_at' in args:
        fields['remind_at'] = parse_due(args['remind_at'])
    return core.Task(**fields)


def tool_create_task(args):
    courses = course_index()
    args = dict(args)
    args.setdefault('duration_minutes', core.setting('default_task_minutes', 25))
    task = core.save_entry('tasks', build_task({}, args, courses))
    return {'created': task_view(task, courses)}


def tool_update_task(args):
    courses = course_index()
    existing = core.get('tasks', args['task_id'])
    body = build_task(existing, args, courses)
    task = core.save_entry('tasks', body, existing['id'])
    return {'updated': task_view(task, courses)}


def tool_list_courses(args):
    tasks = core.rows('tasks')
    result = []
    for c in sorted(course_index().values(), key=lambda c: c['title']):
        mine = [t for t in tasks if t.get('course_id') == c['id']]
        result.append({'id': c['id'], 'title': c['title'], 'kind': c.get('kind', ''),
                       'obsidian_folder': c.get('vault_folder', ''),
                       'open_tasks': sum(t.get('status') != 'done' for t in mine)})
    return {'courses': result}


def tool_get_schedule(args):
    courses = course_index()
    slots = core.rows('slots')
    day = args.get('day', 'week')
    if day != 'week':
        if day == 'today':
            index = cairo_weekday(now_cairo())
        else:
            names = [d.lower() for d in DAY_NAMES]
            index = names.index(day.lower()) if day.lower() in names else int(day)
        slots = [s for s in slots if s['day'] == index]
    slots.sort(key=lambda s: ((s['day'] - 6) % 7, s['start']))  # week starts Saturday
    return {'timezone': 'Africa/Cairo', 'recurring_weekly': True, 'slots': [slot_view(s, courses) for s in slots]}


def vault_notes(course_id='', courses=None):
    prefix = None
    if course_id:
        prefix = (courses or course_index())[course_id].get('vault_folder') or None
        if not prefix:
            return []
    return [f for f in core.vault_files(prefix) if f['kind'] == 'note']


def tool_search_notes(args):
    courses = course_index()
    query = args['query'].strip()
    course_id = resolve_course(args.get('course', ''), courses)
    limit = int(args.get('limit', 10))
    words = [w for w in query.lower().split() if w]
    hits = []
    for f in vault_notes(course_id, courses):
        try:
            text = core.vault_path(f['path']).read_text(encoding='utf-8', errors='replace')[:400000]
        except (HTTPException, OSError):
            continue
        haystack = (f['name'] + '\n' + text).lower()
        score = sum(haystack.count(w) for w in words) + sum(5 for w in words if w in f['name'].lower())
        if score:
            hits.append((score, {'source': 'obsidian', 'path': f['path'], 'title': Path(f['name']).stem,
                                 'folder': f['folder'], 'snippet': snippet(text, words[0])}))
    for n in core.rows('notes'):
        if course_id and n.get('course_id') != course_id:
            continue
        haystack = (n['title'] + '\n' + n.get('content', '')).lower()
        score = sum(haystack.count(w) for w in words)
        if score:
            hits.append((score, {'source': 'studyhub', 'note_id': n['id'], 'title': n['title'],
                                 'course': courses.get(n.get('course_id', ''), {}).get('title', ''),
                                 'snippet': snippet(n.get('content', ''), words[0])}))
    hits.sort(key=lambda h: -h[0])
    return {'query': query, 'results': [h[1] for h in hits[:limit]]}


def tool_read_note(args):
    limit = 60000
    if args.get('note_id'):
        note = core.get('notes', args['note_id'])
        text, title = note.get('content', ''), note['title']
    elif args.get('path'):
        file = core.vault_path(args['path'])
        if not file.is_file() or file.suffix.lower() != '.md':
            raise ValueError('Not a markdown note in the linked Obsidian folders.')
        text, title = file.read_text(encoding='utf-8', errors='replace'), file.stem
    else:
        raise ValueError('Give either path (Obsidian note) or note_id (StudyHub note).')
    return {'title': title, 'truncated': len(text) > limit, 'markdown': text[:limit]}


def tool_create_note(args):
    courses = course_index()
    body = core.Note(title=args['title'], course_id=resolve_course(args.get('course', ''), courses),
                     content=args.get('content', ''))
    note = core.save_entry('notes', body)
    return {'created': {'note_id': note['id'], 'title': note['title'],
                        'course': courses.get(note['course_id'], {}).get('title', '')}}


def tool_list_files(args):
    courses = course_index()
    course_id = resolve_course(args.get('course', ''), courses)
    prefix = courses[course_id].get('vault_folder') if course_id else None
    if course_id and not prefix:
        files = []
    else:
        files = core.vault_files(prefix)
    kind = args.get('type', '')
    if kind:
        files = [f for f in files if f['name'].lower().endswith('.' + kind.lower().lstrip('.'))]
    uploads = [u for u in core.rows('uploads') if not course_id or u.get('course_id') == course_id]
    limit = int(args.get('limit', 100))
    return {'obsidian_files': [{'path': f['path'], 'name': f['name'], 'folder': f['folder'], 'size_kb': round(f['size'] / 1024),
                                'modified': datetime.fromtimestamp(f['modified'], CAIRO).strftime('%Y-%m-%d')} for f in files[:limit]],
            'uploaded_files': [{'id': u['id'], 'name': u['name'], 'course': courses.get(u.get('course_id', ''), {}).get('title', '')}
                               for u in uploads[:limit]]}


def tool_weekly_review(args):
    from .insights import weekly_report
    report = weekly_report(int(args.get('weeks_ago', 0)))
    report.pop('heatmap', None)
    report['note'] = 'Weeks run Saturday to Friday (Cairo). Focus minutes come from the task timer.'
    return report


def tool_create_course(args):
    courses = course_index()
    title = args['title'].strip()
    existing = [c for c in courses.values() if c['title'].lower() == title.lower()]
    if existing:
        return {'exists': {'id': existing[0]['id'], 'title': existing[0]['title']}}
    course = core.add_course(core.Course(title=title, kind=args.get('kind', 'college')))
    return {'created': {'id': course['id'], 'title': course['title'], 'kind': course['kind']}}


def tool_create_slot(args):
    courses = course_index()
    day = args['day']
    names = [d.lower() for d in DAY_NAMES]
    index = names.index(day.lower()) if str(day).lower() in names else int(day)
    body = core.Slot(title=args['title'], course_id=resolve_course(args.get('course', ''), courses),
                     kind=args.get('kind', 'lecture'), day=index, start=args['start'], end=args['end'],
                     location=args.get('location', ''))
    slot = core.save_entry('slots', body)
    return {'created': slot_view(slot, courses)}


# ---------------------------------------------------------------- exams, study plan, flashcards, timetable edits, timers

def day_index(value):
    names = [d.lower() for d in DAY_NAMES]
    text = str(value).strip().lower()
    if text in names:
        return names.index(text)
    if text.isdigit() and 0 <= int(text) <= 6:
        return int(text)
    raise ValueError(f'Unknown day "{value}". Use an English day name, e.g. "Sunday".')


def exam_view(exam, courses):
    from .exams import label
    days = (date.fromisoformat(exam['date']) - now_cairo().date()).days
    return {'id': exam['id'], 'label': label(exam, courses, 'en'), 'course': courses.get(exam.get('course_id', ''), {}).get('title', ''),
            'title': exam.get('title', ''), 'kind': exam.get('kind', 'final'), 'date': exam['date'], 'time': exam.get('time', ''),
            'days_left': days, 'duration_minutes': exam.get('duration_minutes', 120), 'location': exam.get('location', ''),
            'weight': exam.get('weight', 3)}


def tool_list_exams(args):
    courses = course_index()
    today = now_cairo().date().isoformat()
    exams = [e for e in core.rows('exams') if args.get('include_past') or e['date'] >= today]
    exams.sort(key=lambda e: (e['date'], e.get('time') or '99:99'))
    return {'today': today, 'exams': [exam_view(e, courses) for e in exams]}


EXAM_FIELDS = ('title', 'kind', 'date', 'time', 'duration_minutes', 'location', 'weight')


def exam_body(existing, args, courses):
    from .exams import Exam
    data = {k: existing[k] for k in ('course_id', *EXAM_FIELDS) if k in existing}
    if 'course' in args:
        data['course_id'] = resolve_course(args['course'], courses)
    data |= {k: args[k] for k in EXAM_FIELDS if k in args}
    return Exam(**data)


def tool_create_exam(args):
    from .exams import save
    courses = course_index()
    if 'date' not in args:
        raise ValueError('date is required (YYYY-MM-DD)')
    exam = save(exam_body({}, args, courses))
    return {'created': exam_view(exam, courses)}


def tool_update_exam(args):
    from .exams import save
    courses = course_index()
    existing = core.get('exams', args['exam_id'])
    exam = save(exam_body(existing, args, courses), existing['id'])
    return {'updated': exam_view(exam, courses)}


def tool_delete_exam(args):
    from .exams import delete_exam
    courses = course_index()
    exam = exam_view(core.get('exams', args['exam_id']), courses)
    delete_exam(args['exam_id'])
    return {'deleted': exam}


def tool_plan_study(args):
    from .exams import PlanRequest, make_plan
    rest = [day_index(d) for d in args.get('rest_days', [])]
    body = PlanRequest(minutes_per_day=args.get('minutes_per_day', 180), session_minutes=args.get('session_minutes', 50),
                       rest_days=rest, dry_run=not args.get('apply', False), lang='en' if args.get('language') == 'en' else 'ar')
    result = make_plan(body)
    by_day = {}
    for item in result.pop('preview', []):
        by_day.setdefault(item['date'], []).append(item['kind'])
    out = {'applied': bool(args.get('apply')), 'sessions': result['sessions'], 'days': result['days'],
           'from': result['first'], 'to': result['last'], 'session_minutes': body.session_minutes,
           'per_exam': [{'exam': x['label'], 'sessions': x['sessions'], 'hours': round(x['minutes'] / 60, 1)} for x in result['by_exam']]}
    if not args.get('apply'):
        out['first_days'] = [{'date': d, 'sessions': len(kinds), 'final_review': 'review' in kinds} for d, kinds in list(by_day.items())[:7]]
        out['next_step'] = 'Show this to the user; call plan_study again with apply=true to add the sessions as tasks.'
    else:
        out['created_tasks'] = result.get('created', 0)
        out['replaced_unfinished'] = result.get('removed', 0)
    return out


def card_view(card, courses):
    return {'id': card['id'], 'front': card['front'], 'back': card['back'],
            'course': courses.get(card.get('course_id', ''), {}).get('title', ''), 'due': card['due'],
            'interval_days': card.get('interval', 0), 'reviews': card.get('reps', 0)}


def tool_list_flashcards(args):
    courses = course_index()
    course_id = resolve_course(args.get('course', ''), courses)
    today = now_cairo().date().isoformat()
    cards = [c for c in core.rows('cards') if not course_id or c.get('course_id') == course_id]
    due = [c for c in cards if c['due'] <= today]
    pick = due if args.get('due_only', True) else cards
    pick.sort(key=lambda c: (c['due'], c.get('created', 0)))
    limit = max(1, min(int(args.get('limit', 20)), 100))
    return {'total': len(cards), 'due_today': len(due), 'cards': [card_view(c, courses) for c in pick[:limit]]}


def tool_create_flashcards(args):
    from .cards import CardBatch, CardIn, add_cards
    courses = course_index()
    course_id = resolve_course(args.get('course', ''), courses)
    cards = args.get('cards') or []
    if not cards:
        raise ValueError('cards must hold at least one {"front", "back"}')
    result = add_cards(CardBatch(cards=[CardIn(front=str(c.get('front', '')), back=str(c.get('back', '')), course_id=course_id,
                                              source='claude') for c in cards[:100]]))
    return {'created': {'title': f"{result['added']} cards", 'course': courses.get(course_id, {}).get('title', '')},
            'added': result['added'], 'skipped_duplicates': result['skipped']}


def tool_review_flashcard(args):
    from .cards import Review, review
    courses = course_index()
    card = review(args['card_id'], Review(grade=args['grade']))
    return {'updated': card_view(card, courses) | {'title': card['front'][:60]}, 'next_review': card['due']}


def tool_update_slot(args):
    courses = course_index()
    existing = core.get('slots', args['slot_id'])
    data = {k: existing.get(k) for k in ('title', 'course_id', 'kind', 'day', 'start', 'end', 'location')}
    if 'course' in args:
        data['course_id'] = resolve_course(args['course'], courses)
    if 'day' in args:
        data['day'] = day_index(args['day'])
    data |= {k: args[k] for k in ('title', 'kind', 'start', 'end', 'location') if k in args}
    slot = core.save_entry('slots', core.Slot(**{k: v for k, v in data.items() if v is not None}), existing['id'])
    return {'updated': slot_view(slot, courses)}


def tool_delete_slot(args):
    courses = course_index()
    slot = slot_view(core.get('slots', args['slot_id']), courses)
    core.delete_entry('slots', args['slot_id'])
    return {'deleted': slot}


def tool_task_timer(args):
    courses = course_index()
    action = args['action']
    body = core.TimerAction(action=action, duration_minutes=args.get('minutes'))
    task = core.task_timer(args['task_id'], body)
    return {'updated': task_view(task, courses), 'timer': task.get('timer', {}).get('state')}


def grade_view(grade, courses):
    return {'id': grade['id'], 'course': courses.get(grade['course_id'], {}).get('title', ''), 'kind': grade['kind'],
            'title': grade.get('title') or grade['kind'].capitalize(), 'score': grade['score'], 'out_of': grade['out_of'],
            'date': grade.get('date', ''), 'note': grade.get('note', '')}


def college_id(value, courses):
    course_id = resolve_course(value, courses)
    if not course_id:
        raise ValueError('course is required: grades belong to a college course')
    if courses[course_id].get('kind') != 'college':
        raise ValueError(f'"{courses[course_id]["title"]}" is a personal course; grades are for college courses only')
    return course_id


def tool_list_grades(args):
    from .grades import overview
    courses = course_index()
    course_id = college_id(args['course'], courses) if args.get('course') else ''
    data = overview(course_id)
    return {'courses': [{'course': c['title'], 'total_so_far': f"{c['earned']:g} / {c['counted']:g}" if c['counted'] else None,
                         'percent': c['percent'], 'course_total': c['total'], 'marks_split': c['split'],
                         'by_kind': {k: v for k, v in c['kinds'].items() if v['count'] or (c['split'] or {}).get(k)}}
                        for c in data['courses']],
            'grades': [grade_view(g, courses) for g in data['grades']]}


GRADE_FIELDS = ('kind', 'title', 'score', 'out_of', 'date', 'note')


def tool_add_grades(args):
    from .grades import Grade, GradeBatch, add_grades
    courses = course_index()
    items = args.get('grades') or []
    if not items:
        raise ValueError('grades must hold at least one {"kind", "score", "out_of"}')
    batch = [Grade(course_id=college_id(g.get('course') or args.get('course', ''), courses), **{k: g[k] for k in GRADE_FIELDS if k in g})
             for g in items[:100]]
    result = add_grades(GradeBatch(grades=batch))
    return {'created': {'title': f"{len(result['grades'])} grades"}, 'added': result['added'], 'updated_existing': result['updated'],
            'grades': [grade_view(g, courses) for g in result['grades']]}


def tool_update_grade(args):
    from .grades import Grade, edit
    courses = course_index()
    existing = core.get('grades', args['grade_id'])
    data = {k: existing[k] for k in ('course_id', *GRADE_FIELDS) if k in existing}
    if 'course' in args:
        data['course_id'] = college_id(args['course'], courses)
    data |= {k: args[k] for k in GRADE_FIELDS if k in args}
    return {'updated': grade_view(edit(existing['id'], Grade(**data)), courses)}


def tool_delete_grade(args):
    from .grades import delete_grade
    courses = course_index()
    grade = grade_view(core.get('grades', args['grade_id']), courses)
    delete_grade(args['grade_id'])
    return {'deleted': grade}


def tool_set_grade_split(args):
    from .grades import Split, set_split
    courses = course_index()
    course_id = college_id(args['course'], courses)
    result = set_split(course_id, Split(**{k: args.get(k, 0) for k in ('sheet', 'quiz', 'midterm', 'final')}))
    return {'updated': {'title': result['title'], 'marks_split': result['split'], 'course_total': result['total'],
                        'total_so_far': f"{result['earned']:g} / {result['counted']:g}", 'percent': result['percent']}}


def string(desc, **extra):
    return {'type': 'string', 'description': desc, **extra}


COURSE = string('Course id or (part of) its title, e.g. "NLP" or "ROB 307". Omit for all courses.')
TOOLS = [
    {'name': 'get_today', 'title': 'Today overview', 'handler': tool_get_today,
     'description': 'Current Cairo time, tasks planned today or due in the next 48h, overdue tasks and today\'s timetable. Call this first.',
     'inputSchema': {'type': 'object', 'properties': {}}, 'annotations': {'readOnlyHint': True}},
    {'name': 'list_tasks', 'title': 'List tasks', 'handler': tool_list_tasks,
     'description': 'List tasks sorted by deadline. Filter by status, course or text.',
     'inputSchema': {'type': 'object', 'properties': {
         'status': string('open (default) = todo+doing, or todo, doing, done, all', enum=['open', 'todo', 'doing', 'done', 'all']),
         'course': COURSE, 'query': string('Text to look for in title/details'),
         'limit': {'type': 'integer', 'minimum': 1, 'maximum': 200, 'default': 50}}},
     'annotations': {'readOnlyHint': True}},
    {'name': 'create_task', 'title': 'Create task', 'handler': tool_create_task,
     'description': 'Create a task. Dates are Cairo time: due "YYYY-MM-DD" means end of that day.',
     'inputSchema': {'type': 'object', 'required': ['title'], 'properties': {
         'title': string('Short task title'), 'course': COURSE,
         'due': string('Deadline: YYYY-MM-DD or YYYY-MM-DDTHH:MM'),
         'priority': string('normal or high', enum=['normal', 'high']),
         'details': string('Longer description / checklist'),
         'planned_date': string('Day you plan to work on it, YYYY-MM-DD'),
         'planned_time': string('Start time HH:MM (needs planned_date)'),
         'duration_minutes': {'type': 'integer', 'minimum': 1, 'maximum': 1440},
         'remind_at': string('Push reminder time YYYY-MM-DDTHH:MM')}},
     'annotations': {'readOnlyHint': False, 'destructiveHint': False}},
    {'name': 'update_task', 'title': 'Update task', 'handler': tool_update_task,
     'description': 'Change a task (mark done, reschedule, rename…). Only the fields you pass change.',
     'inputSchema': {'type': 'object', 'required': ['task_id'], 'properties': {
         'task_id': string('Task id from list_tasks/get_today'),
         'status': string('todo, doing or done', enum=['todo', 'doing', 'done']),
         'title': string('New title'), 'course': COURSE, 'due': string('YYYY-MM-DD or YYYY-MM-DDTHH:MM, "" to clear'),
         'priority': string('normal or high', enum=['normal', 'high']), 'details': string('New details'),
         'planned_date': string('YYYY-MM-DD, "" to clear'), 'planned_time': string('HH:MM, "" to clear'),
         'duration_minutes': {'type': 'integer', 'minimum': 1, 'maximum': 1440}}},
     'annotations': {'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': True}},
    {'name': 'list_courses', 'title': 'List courses', 'handler': tool_list_courses,
     'description': 'All university and personal courses with their Obsidian folder and open task count.',
     'inputSchema': {'type': 'object', 'properties': {}}, 'annotations': {'readOnlyHint': True}},
    {'name': 'get_schedule', 'title': 'Weekly timetable', 'handler': tool_get_schedule,
     'description': 'Recurring weekly timetable (lectures, sections, labs, study sessions).',
     'inputSchema': {'type': 'object', 'properties': {
         'day': string('"week" (default), "today", or a day name like "Wednesday"')}},
     'annotations': {'readOnlyHint': True}},
    {'name': 'search_notes', 'title': 'Search notes', 'handler': tool_search_notes,
     'description': 'Full-text search over Obsidian lecture notes and StudyHub notes. Returns paths/ids for read_note.',
     'inputSchema': {'type': 'object', 'required': ['query'], 'properties': {
         'query': string('Words to search for (English or Arabic)'), 'course': COURSE,
         'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50, 'default': 10}}},
     'annotations': {'readOnlyHint': True}},
    {'name': 'read_note', 'title': 'Read note', 'handler': tool_read_note,
     'description': 'Full markdown of one note: pass path (Obsidian) or note_id (StudyHub).',
     'inputSchema': {'type': 'object', 'properties': {
         'path': string('Obsidian note path from search_notes/list_files'),
         'note_id': string('StudyHub note id from search_notes')}},
     'annotations': {'readOnlyHint': True}},
    {'name': 'create_note', 'title': 'Create note', 'handler': tool_create_note,
     'description': 'Save a new StudyHub note (markdown). Obsidian files are never modified.',
     'inputSchema': {'type': 'object', 'required': ['title'], 'properties': {
         'title': string('Note title'), 'course': COURSE, 'content': string('Markdown body')}},
     'annotations': {'readOnlyHint': False, 'destructiveHint': False}},
    {'name': 'list_files', 'title': 'List course files', 'handler': tool_list_files,
     'description': 'Files in the linked Obsidian folders (PDFs, slides, code…) and manual uploads.',
     'inputSchema': {'type': 'object', 'properties': {
         'course': COURSE, 'type': string('Extension filter, e.g. pdf, pptx, md'),
         'limit': {'type': 'integer', 'minimum': 1, 'maximum': 500, 'default': 100}}},
     'annotations': {'readOnlyHint': True}},
    {'name': 'weekly_review', 'title': 'Weekly review', 'handler': tool_weekly_review,
     'description': 'What got done in a Saturday–Friday week: focus minutes per day and per course, completed tasks, missed deadlines, streak.',
     'inputSchema': {'type': 'object', 'properties': {
         'weeks_ago': {'type': 'integer', 'minimum': 0, 'maximum': 52, 'default': 0}}},
     'annotations': {'readOnlyHint': True}},
    {'name': 'create_course', 'title': 'Create course', 'handler': tool_create_course,
     'description': 'Add a course (college subject or personal course). Returns the existing one if the title already exists.',
     'inputSchema': {'type': 'object', 'required': ['title'], 'properties': {
         'title': string('Course name, e.g. "Computer Vision"'),
         'kind': string('college (default) or personal', enum=['college', 'personal'])}},
     'annotations': {'readOnlyHint': False, 'destructiveHint': False}},
    {'name': 'create_slot', 'title': 'Add to timetable', 'handler': tool_create_slot,
     'description': 'Add a recurring weekly slot (lecture, section, lab or study session) to the timetable. Times are Cairo, 24h HH:MM.',
     'inputSchema': {'type': 'object', 'required': ['title', 'day', 'start', 'end'], 'properties': {
         'title': string('Slot title, usually the course name'), 'course': COURSE,
         'day': string('Day name in English, e.g. "Sunday"', enum=DAY_NAMES),
         'start': string('HH:MM 24h'), 'end': string('HH:MM 24h, after start'),
         'kind': string('lecture, section, lab or study', enum=['lecture', 'section', 'lab', 'study']),
         'location': string('Hall / lab name')}},
     'annotations': {'readOnlyHint': False, 'destructiveHint': False}},
    {'name': 'list_exams', 'title': 'List exams', 'handler': tool_list_exams,
     'description': 'Upcoming exams with days left, course, type, place and how heavy each one is (weight 1-5).',
     'inputSchema': {'type': 'object', 'properties': {'include_past': {'type': 'boolean', 'default': False}}},
     'annotations': {'readOnlyHint': True}},
    {'name': 'create_exam', 'title': 'Add exam', 'handler': tool_create_exam,
     'description': 'Add an exam to the exam timetable. Give a course, or a title for an exam without a course.',
     'inputSchema': {'type': 'object', 'required': ['date'], 'properties': {
         'course': COURSE, 'title': string('Exam name when it has no course, e.g. "IELTS"'),
         'kind': string('final (default), midterm, quiz, practical or oral', enum=['final', 'midterm', 'quiz', 'practical', 'oral']),
         'date': string('YYYY-MM-DD'), 'time': string('Start time HH:MM 24h, optional'),
         'duration_minutes': {'type': 'integer', 'minimum': 0, 'maximum': 600},
         'location': string('Hall / room'), 'weight': {'type': 'integer', 'minimum': 1, 'maximum': 5, 'description': 'How heavy: 1 light … 5 very heavy (default 3)'}}},
     'annotations': {'readOnlyHint': False, 'destructiveHint': False}},
    {'name': 'update_exam', 'title': 'Change exam', 'handler': tool_update_exam,
     'description': 'Change an exam (date, time, place, weight…). Only the fields you pass change.',
     'inputSchema': {'type': 'object', 'required': ['exam_id'], 'properties': {
         'exam_id': string('Exam id from list_exams'), 'course': COURSE, 'title': string('Exam name'),
         'kind': string('final, midterm, quiz, practical or oral', enum=['final', 'midterm', 'quiz', 'practical', 'oral']),
         'date': string('YYYY-MM-DD'), 'time': string('HH:MM or "" for no time'),
         'duration_minutes': {'type': 'integer', 'minimum': 0, 'maximum': 600}, 'location': string('Hall / room'),
         'weight': {'type': 'integer', 'minimum': 1, 'maximum': 5}}},
     'annotations': {'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': True}},
    {'name': 'delete_exam', 'title': 'Delete exam', 'handler': tool_delete_exam,
     'description': 'Remove an exam from the timetable. Ask the user first.',
     'inputSchema': {'type': 'object', 'required': ['exam_id'], 'properties': {'exam_id': string('Exam id from list_exams')}},
     'annotations': {'readOnlyHint': False, 'destructiveHint': True}},
    {'name': 'plan_study', 'title': 'Exam study plan', 'handler': tool_plan_study,
     'description': 'Spread study sessions over the days left before the upcoming exams (the day before each exam is its final review). '
                    'apply=false (default) only previews; apply=true adds the sessions as tasks and replaces the unfinished part of an older plan.',
     'inputSchema': {'type': 'object', 'properties': {
         'minutes_per_day': {'type': 'integer', 'minimum': 25, 'maximum': 720, 'default': 180},
         'session_minutes': {'type': 'integer', 'minimum': 15, 'maximum': 180, 'default': 50},
         'rest_days': {'type': 'array', 'items': string('Day name, e.g. "Friday"', enum=DAY_NAMES), 'description': 'Days with no sessions'},
         'apply': {'type': 'boolean', 'default': False},
         'language': string('Language of the session titles: ar (default) or en', enum=['ar', 'en'])}},
     'annotations': {'readOnlyHint': False, 'destructiveHint': False}},
    {'name': 'list_flashcards', 'title': 'List flashcards', 'handler': tool_list_flashcards,
     'description': 'Flashcards (spaced repetition). By default only the cards due today, oldest first: use them to quiz the user.',
     'inputSchema': {'type': 'object', 'properties': {
         'course': COURSE, 'due_only': {'type': 'boolean', 'default': True},
         'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 20}}},
     'annotations': {'readOnlyHint': True}},
    {'name': 'create_flashcards', 'title': 'Create flashcards', 'handler': tool_create_flashcards,
     'description': 'Add flashcards, e.g. written from a note you read with read_note. One fact per card, a question that stands alone, a short answer. Duplicates are skipped.',
     'inputSchema': {'type': 'object', 'required': ['cards'], 'properties': {
         'course': COURSE,
         'cards': {'type': 'array', 'minItems': 1, 'maxItems': 100, 'items': {'type': 'object', 'required': ['front', 'back'], 'properties': {
             'front': string('The question'), 'back': string('The answer')}}}}},
     'annotations': {'readOnlyHint': False, 'destructiveHint': False}},
    {'name': 'review_flashcard', 'title': 'Grade a flashcard', 'handler': tool_review_flashcard,
     'description': 'Record how the user did on one card after they answered: again (wrong), hard, good or easy. This sets when the card comes back.',
     'inputSchema': {'type': 'object', 'required': ['card_id', 'grade'], 'properties': {
         'card_id': string('Card id from list_flashcards'), 'grade': string('again, hard, good or easy', enum=['again', 'hard', 'good', 'easy'])}},
     'annotations': {'readOnlyHint': False, 'destructiveHint': False}},
    {'name': 'update_slot', 'title': 'Change timetable slot', 'handler': tool_update_slot,
     'description': 'Change a weekly timetable slot (day, time, place, type…). Only the fields you pass change. Slot ids come from get_schedule.',
     'inputSchema': {'type': 'object', 'required': ['slot_id'], 'properties': {
         'slot_id': string('Slot id from get_schedule'), 'title': string('Slot title'), 'course': COURSE,
         'day': string('Day name in English', enum=DAY_NAMES), 'start': string('HH:MM 24h'), 'end': string('HH:MM 24h'),
         'kind': string('lecture, section, lab or study', enum=['lecture', 'section', 'lab', 'study']), 'location': string('Hall / lab name')}},
     'annotations': {'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': True}},
    {'name': 'delete_slot', 'title': 'Remove timetable slot', 'handler': tool_delete_slot,
     'description': 'Remove a weekly timetable slot. Ask the user first.',
     'inputSchema': {'type': 'object', 'required': ['slot_id'], 'properties': {'slot_id': string('Slot id from get_schedule')}},
     'annotations': {'readOnlyHint': False, 'destructiveHint': True}},
    {'name': 'task_timer', 'title': 'Task focus timer', 'handler': tool_task_timer,
     'description': "Start, pause or reset a task's focus timer (the time counts in the weekly review). minutes sets a new length.",
     'inputSchema': {'type': 'object', 'required': ['task_id', 'action'], 'properties': {
         'task_id': string('Task id from list_tasks/get_today'), 'action': string('start, pause or reset', enum=['start', 'pause', 'reset']),
         'minutes': {'type': 'integer', 'minimum': 1, 'maximum': 1440, 'description': 'Session length; only when starting fresh or resetting'}}},
     'annotations': {'readOnlyHint': False, 'destructiveHint': False}},
    {'name': 'list_grades', 'title': 'List grades', 'handler': tool_list_grades,
     'description': 'College grades (sheets, quizzes, midterm, final) with each course\'s total so far and percent. '
                    'With a marks split, a kind counts as its share of the course marks.',
     'inputSchema': {'type': 'object', 'properties': {'course': COURSE}}, 'annotations': {'readOnlyHint': True}},
    {'name': 'add_grades', 'title': 'Add grades', 'handler': tool_add_grades,
     'description': 'Record grades for college courses, e.g. from a grade sheet or a screenshot the user sends. '
                    'The same course + kind + title again updates that grade instead of adding a copy. '
                    'Sheets and quizzes without a title are numbered (Sheet 1, Sheet 2…).',
     'inputSchema': {'type': 'object', 'required': ['grades'], 'properties': {
         'course': string('College course for every grade below, unless a grade names its own'),
         'grades': {'type': 'array', 'minItems': 1, 'maxItems': 100, 'items': {'type': 'object', 'required': ['kind', 'score', 'out_of'], 'properties': {
             'course': string('College course id or (part of) its title'),
             'kind': string('sheet, quiz, midterm or final', enum=['sheet', 'quiz', 'midterm', 'final']),
             'title': string('e.g. "Sheet 3" or "Quiz 2"; optional'), 'score': {'type': 'number', 'minimum': 0},
             'out_of': {'type': 'number', 'minimum': 0.01, 'description': 'The maximum, e.g. 10'}, 'date': string('YYYY-MM-DD, optional'), 'note': string('Optional')}}}}},
     'annotations': {'readOnlyHint': False, 'destructiveHint': False}},
    {'name': 'update_grade', 'title': 'Change grade', 'handler': tool_update_grade,
     'description': 'Change one grade (score, maximum, title…). Only the fields you pass change. Grade ids come from list_grades.',
     'inputSchema': {'type': 'object', 'required': ['grade_id'], 'properties': {
         'grade_id': string('Grade id from list_grades'), 'course': string('College course'),
         'kind': string('sheet, quiz, midterm or final', enum=['sheet', 'quiz', 'midterm', 'final']), 'title': string('Title'),
         'score': {'type': 'number', 'minimum': 0}, 'out_of': {'type': 'number', 'minimum': 0.01, 'description': 'The maximum, e.g. 10'},
         'date': string('YYYY-MM-DD or ""'), 'note': string('Note')}},
     'annotations': {'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': True}},
    {'name': 'delete_grade', 'title': 'Delete grade', 'handler': tool_delete_grade,
     'description': 'Remove one grade. Ask the user first.',
     'inputSchema': {'type': 'object', 'required': ['grade_id'], 'properties': {'grade_id': string('Grade id from list_grades')}},
     'annotations': {'readOnlyHint': False, 'destructiveHint': True}},
    {'name': 'set_grade_split', 'title': 'Course marks split', 'handler': tool_set_grade_split,
     'description': 'How many of a college course\'s marks each kind is worth, e.g. sheet 10, quiz 10, midterm 20, final 60. '
                    'All zeros removes the split (the total is then the plain sum of the scores).',
     'inputSchema': {'type': 'object', 'required': ['course'], 'properties': {
         'course': string('College course id or (part of) its title'),
         **{k: {'type': 'number', 'minimum': 0, 'description': f'Marks for the {k}' + ('s' if k in ('sheet', 'quiz') else '')}
            for k in ('sheet', 'quiz', 'midterm', 'final')}}},
     'annotations': {'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': True}},
]
TOOL_INDEX = {t['name']: t for t in TOOLS}


# ---------------------------------------------------------------- JSON-RPC

def rpc_error(msg_id, code, message):
    return {'jsonrpc': '2.0', 'id': msg_id, 'error': {'code': code, 'message': message}}


def handle(message):
    if not isinstance(message, dict) or message.get('jsonrpc') != '2.0' or 'method' not in message:
        return rpc_error(message.get('id') if isinstance(message, dict) else None, -32600, 'Invalid request')
    msg_id, method, params = message.get('id'), message['method'], message.get('params') or {}
    if 'id' not in message:  # notification (e.g. notifications/initialized)
        return None
    if method == 'initialize':
        requested = params.get('protocolVersion')
        return {'jsonrpc': '2.0', 'id': msg_id, 'result': {
            'protocolVersion': requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
            'capabilities': {'tools': {'listChanged': False}},
            'serverInfo': SERVER_INFO, 'instructions': INSTRUCTIONS}}
    if method == 'ping':
        return {'jsonrpc': '2.0', 'id': msg_id, 'result': {}}
    if method == 'tools/list':
        tools = [{k: v for k, v in t.items() if k != 'handler'} for t in TOOLS]
        return {'jsonrpc': '2.0', 'id': msg_id, 'result': {'tools': tools}}
    if method == 'tools/call':
        tool = TOOL_INDEX.get(params.get('name'))
        if not tool:
            return rpc_error(msg_id, -32602, f"Unknown tool: {params.get('name')}")
        try:
            data = tool['handler'](params.get('arguments') or {})
            return {'jsonrpc': '2.0', 'id': msg_id, 'result': {
                'content': [{'type': 'text', 'text': json.dumps(data, ensure_ascii=False, indent=1)}],
                'structuredContent': data, 'isError': False}}
        except HTTPException as error:
            text = error.detail if isinstance(error.detail, str) else 'Request rejected'
        except (ValueError, KeyError, TypeError) as error:
            text = str(error) or error.__class__.__name__
        return {'jsonrpc': '2.0', 'id': msg_id, 'result': {
            'content': [{'type': 'text', 'text': f'Error: {text}'}], 'isError': True}}
    if method in ('resources/list', 'prompts/list'):
        return {'jsonrpc': '2.0', 'id': msg_id, 'result': {method.split('/')[0]: []}}
    return rpc_error(msg_id, -32601, f'Method not found: {method}')


async def endpoint(request: Request):
    try:
        payload = json.loads(await request.body())
    except (ValueError, UnicodeDecodeError):
        return JSONResponse(rpc_error(None, -32700, 'Parse error'), status_code=400)
    if isinstance(payload, list):
        replies = [r for r in (handle(m) for m in payload) if r is not None]
        return JSONResponse(replies) if replies else Response(status_code=202)
    reply = handle(payload)
    return JSONResponse(reply) if reply is not None else Response(status_code=202)


def unauthorized():
    return JSONResponse({'error': 'invalid_token'}, status_code=401, headers={'WWW-Authenticate': 'Bearer'})


def allowed(token):
    from . import accounts
    if accounts.enabled():  # the middleware resolved the user from this token
        return accounts.CURRENT_USER.get() is not None
    return token_valid(token)


@router.post('/mcp')
async def mcp_header_auth(request: Request):
    if not allowed(bearer_token(request)):
        return unauthorized()
    return await endpoint(request)


@router.post('/mcp/{token}')
async def mcp_url_auth(token: str, request: Request):
    if not allowed(token):
        return unauthorized()
    return await endpoint(request)


@router.get('/mcp')
@router.get('/mcp/{token}')
@router.delete('/mcp')
@router.delete('/mcp/{token}')
def mcp_no_stream(token: str = ''):
    # Stateless server: no server-initiated SSE stream and no sessions to close.
    return Response(status_code=405, headers={'Allow': 'POST'})
