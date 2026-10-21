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
    "a weekly timetable, Obsidian lecture notes and uploaded files. Times are Africa/Cairo. "
    "Call get_today first for context. Prefer search_notes + read_note before answering questions "
    "about course material, and cite the note title/path you used. Ask before deleting or "
    "overwriting anything; create_task/create_note/update_task change real data."
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
