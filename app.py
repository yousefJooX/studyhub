"""A separate web service; the existing archive and Telegram bot are read only."""
import asyncio
import hashlib
import hmac
import html
import json
import mimetypes
import math
import os
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import closing, contextmanager, asynccontextmanager
from datetime import date, datetime, time as clock_time
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from markdown_it import MarkdownIt
from pydantic import BaseModel, Field, field_validator

STATIC = Path(__file__).parent / 'static'
DOCUMENTS = {'.md', '.pdf', '.ppt', '.pptx', '.doc', '.docx', '.xls', '.xlsx', '.csv',
             '.txt', '.ipynb', '.py', '.zip', '.mp4', '.mp3', '.wav', '.png', '.jpg',
             '.jpeg', '.webp', '.gif', '.svg', '.canvas', '.html'}
SKIP = {'node_modules', '__pycache__', 'venv', 'env', 'site-packages', 'dist', 'build'}
@asynccontextmanager
async def lifespan(application):
    from .reminders import reminder_loop
    worker = asyncio.create_task(reminder_loop()) if os.environ.get('STUDY_REMINDERS_ENABLED', 'true') == 'true' else None
    try:
        yield
    finally:
        if worker:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)


app = FastAPI(title='مساحتي الدراسية', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


def data_root():
    """The signed-in user's private folder (multi-user site) or the single owner's data folder."""
    from . import accounts
    if accounts.enabled():
        user = accounts.CURRENT_USER.get()
        if not user:
            raise HTTPException(401, 'سجّل الدخول أولًا')
        return accounts.user_root(user['id'])
    return accounts.base_root()


@contextmanager
def database():
    db = sqlite3.connect(data_root() / 'study.sqlite3', timeout=20)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    try:
        with db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS courses(id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS slots(id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS notes(id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS uploads(id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS login_attempts(client TEXT PRIMARY KEY, started REAL, count INTEGER);
            CREATE TABLE IF NOT EXISTS push_subscriptions(id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS study_sessions(id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS reminder_deliveries(id TEXT PRIMARY KEY, task_id TEXT NOT NULL,
                subscription_id TEXT NOT NULL, kind TEXT NOT NULL, stamp TEXT NOT NULL,
                due REAL NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0, next_try REAL NOT NULL DEFAULT 0);
            ''')
            yield db
    finally:
        db.close()


def rows(table):
    with database() as db:
        return [json.loads(r['data']) for r in db.execute(f'SELECT data FROM {table}')]


def get(table, key):
    with database() as db:
        r = db.execute(f'SELECT data FROM {table} WHERE id=?', (key,)).fetchone()
    if not r:
        raise HTTPException(404, 'العنصر غير موجود')
    return json.loads(r['data'])


def put(table, value):
    with database() as db:
        db.execute(f'INSERT OR REPLACE INTO {table}(id,data) VALUES (?,?)',
                   (value['id'], json.dumps(value, ensure_ascii=False)))
    return value


def setting(key, default):
    with database() as db:
        row = db.execute('SELECT data FROM settings WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else default


def set_setting(key, value):
    with database() as db:
        db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, json.dumps(value)))


def session_digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def authorized(request: Request):
    from . import accounts
    if accounts.enabled():  # the middleware already resolved the user from cookie / API token
        if accounts.CURRENT_USER.get():
            return True
        raise HTTPException(401, 'سجّل الدخول أولًا')
    # Scripts, n8n and Claude Code can use the API token instead of a browser session.
    header = request.headers.get('authorization', '')
    if header.lower().startswith('bearer '):
        from .mcp_server import token_valid
        if token_valid(header[7:].strip()):
            return True
        raise HTTPException(401, 'API token غير صحيح')
    token = request.cookies.get('study_session', '')
    if token:
        with database() as db:
            row = db.execute('SELECT expires FROM sessions WHERE token=?', (session_digest(token),)).fetchone()
        if row and row[0] > time.time():
            return True
    raise HTTPException(401, 'سجّل الدخول أولًا')


@app.middleware('http')
async def protection(request: Request, call_next):
    # Same-origin JSON/raw uploads prevent cross-site writes. No CORS is enabled.
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        origin = request.headers.get('origin')
        allowed = {request.url.scheme + '://' + request.headers.get('host', '')}
        if os.environ.get('STUDY_PUBLIC_ORIGIN'):
            allowed.add(os.environ['STUDY_PUBLIC_ORIGIN'].rstrip('/'))
        if origin and origin.rstrip('/') not in allowed:
            return Response('Cross-origin request rejected', status_code=403)
    from . import accounts
    if accounts.enabled():
        import asyncio as _asyncio
        accounts.CURRENT_USER.set(await _asyncio.to_thread(accounts.resolve, request))
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Cross-Origin-Opener-Policy'] = 'same-origin'
    response.headers['Permissions-Policy'] = 'camera=(), geolocation=(), payment=(), usb=(), microphone=(self)'
    if os.environ.get('STUDY_SECURE_COOKIE', 'true') == 'true':
        response.headers['Strict-Transport-Security'] = 'max-age=31536000'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
        "connect-src 'self'; font-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'")
    response.headers['Cache-Control'] = 'no-store' if request.url.path.startswith(('/api/', '/mcp')) else 'no-cache'
    return response


class Login(BaseModel):
    password: str = Field(min_length=1, max_length=512)
    username: str = Field(default='', max_length=64)


@app.get('/api/auth')
def auth_status(request: Request):
    from . import accounts
    info = {'multiuser': accounts.enabled(), 'signup': accounts.signup_open(), 'invite': accounts.invite_required()}
    try:
        authorized(request)
        user = accounts.CURRENT_USER.get()
        return info | {'authenticated': True, 'user': accounts.public(user) if user else None}
    except HTTPException:
        return info | {'authenticated': False}


@app.post('/api/signup')
def signup(body: dict, request: Request, response: Response):
    from . import accounts
    try:
        data = accounts.Signup(**body)
    except Exception:
        raise HTTPException(400, 'اكتب اسمك، واسم مستخدم من ٣ حروف على الأقل، وباسورد ٨ حروف على الأقل')
    return accounts.signup(data, request, response)


@app.post('/api/login')
def login(body: Login, request: Request, response: Response):
    from . import accounts
    if accounts.enabled():
        return accounts.login(accounts.AccountLogin(username=body.username or '-', password=body.password), request, response)
    encoded = os.environ.get('STUDY_PASSWORD_HASH', '')
    if not encoded:
        raise HTTPException(503, 'اضبط كلمة مرور الموقع على السيرفر أولًا')
    client = accounts.client_ip(request)
    now = time.time()
    with database() as db:
        attempt = db.execute('SELECT started,count FROM login_attempts WHERE client=?', (client,)).fetchone()
        count = attempt['count'] if attempt and attempt['started'] > now - 900 else 0
        started = attempt['started'] if count else now
        if count >= 10:
            raise HTTPException(429, 'محاولات كثيرة؛ حاول بعد ١٥ دقيقة')
        db.execute('INSERT OR REPLACE INTO login_attempts VALUES (?,?,?)', (client, started, count + 1))
    try:
        salt, expected = encoded.split(':', 1)
        actual = hashlib.pbkdf2_hmac('sha256', body.password.encode(), bytes.fromhex(salt), 600_000).hex()
        valid = hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise HTTPException(401, 'كلمة المرور غير صحيحة')
    token = secrets.token_urlsafe(32)
    with database() as db:
        db.execute('DELETE FROM sessions WHERE expires<?', (now,))
        db.execute('DELETE FROM login_attempts WHERE client=?', (client,))
        db.execute('INSERT INTO sessions VALUES (?,?)', (session_digest(token), now + 30 * 86400))
    response.set_cookie('study_session', token, max_age=30 * 86400, httponly=True,
                        secure=os.environ.get('STUDY_SECURE_COOKIE', 'true') == 'true', samesite='strict')
    return {'authenticated': True}


@app.post('/api/logout', dependencies=[Depends(authorized)])
def logout(request: Request, response: Response):
    from . import accounts
    if accounts.enabled():
        return accounts.logout(request, response)
    with database() as db:
        db.execute('DELETE FROM sessions WHERE token=?', (session_digest(request.cookies.get('study_session', '')),))
    response.delete_cookie('study_session')
    return {'ok': True}


def vault_root():
    from . import accounts
    if accounts.enabled():  # each user's own uploaded Obsidian folders
        root = data_root() / 'vault'
        root.mkdir(exist_ok=True, mode=0o700)
        return root.resolve()
    return Path(os.environ.get('STUDY_VAULT_ROOT', str(data_root() / 'vault'))).resolve()


def visible_parts(path):
    return all(not p.startswith('.') and p not in SKIP for p in path.parts)


def mounts():
    from . import accounts
    return setting('mounts', [] if accounts.enabled() else ['1 Univ'])


def vault_path(relative, require_mount=True):
    p = Path(relative)
    if p.is_absolute() or not visible_parts(p) or '..' in p.parts:
        raise HTTPException(404, 'المسار غير متاح')
    root = vault_root()
    target = root / p
    if not target.resolve().is_relative_to(root) or any(x.is_symlink() for x in [target, *target.parents] if x != root):
        raise HTTPException(404, 'المسار غير متاح')
    if require_mount and not any(p == Path(m) or Path(m) in p.parents for m in mounts()):
        raise HTTPException(404, 'الفولدر غير مضاف لمساحتك')
    return target


def vault_files(prefix=None):
    result = []
    for mount in mounts():
        base = vault_path(mount)
        if not base.is_dir():
            continue
        for directory, dirs, files in os.walk(base, followlinks=False):
            dirs[:] = [d for d in dirs if visible_parts(Path(d)) and not (Path(directory) / d).is_symlink()]
            for name in files:
                path = Path(directory) / name
                relative = path.relative_to(vault_root())
                if not visible_parts(relative) or path.is_symlink() or path.suffix.lower() not in DOCUMENTS:
                    continue
                if prefix and not (relative == Path(prefix) or Path(prefix) in relative.parents):
                    continue
                stat = path.stat()
                result.append({'path': str(relative), 'name': name, 'folder': str(relative.parent),
                               'size': stat.st_size, 'modified': stat.st_mtime, 'kind': 'note' if path.suffix.lower() == '.md' else 'file'})
    return sorted(result, key=lambda x: (-x['modified'], x['path']))


class Mount(BaseModel):
    folder: str = Field(min_length=1, max_length=500)


@app.post('/api/vault/mounts', dependencies=[Depends(authorized)])
def add_mount(body: Mount):
    path = vault_path(body.folder, require_mount=False)
    if not path.is_dir():
        raise HTTPException(400, 'الفولدر غير موجود في نسخة الـVault على السيرفر')
    current = mounts()
    relative = str(path.relative_to(vault_root()))
    if relative == '.':
        raise HTTPException(400, 'اختار فولدر محدد')
    if relative not in current:
        set_setting('mounts', current + [relative])
    return {'mounts': mounts()}


class Course(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    kind: str = Field(default='personal', pattern='^(college|personal)$')
    vault_folder: str = Field(default='', max_length=500)


def check_course(course_id):
    if course_id:
        get('courses', course_id)


@app.post('/api/courses', dependencies=[Depends(authorized)])
def add_course(body: Course):
    if body.vault_folder and not vault_path(body.vault_folder).is_dir():
        raise HTTPException(400, 'فولدر المادة غير موجود')
    return put('courses', {'id': uuid.uuid4().hex, **body.model_dump(), 'created': time.time()})


@app.put('/api/courses/{course_id}', dependencies=[Depends(authorized)])
def edit_course(course_id: str, body: Course):
    old = get('courses', course_id)
    if body.vault_folder and not vault_path(body.vault_folder).is_dir():
        raise HTTPException(400, 'فولدر المادة غير موجود')
    return put('courses', old | body.model_dump())


@app.delete('/api/courses/{course_id}', dependencies=[Depends(authorized)])
def remove_course(course_id: str):
    # Keep the learner's work and detach it in the same transaction as removal.
    detached = {}
    with database() as db:
        if not db.execute('SELECT 1 FROM courses WHERE id=?', (course_id,)).fetchone():
            raise HTTPException(404, 'المادة غير موجودة')
        for table in ('tasks', 'slots', 'notes', 'uploads'):
            detached[table] = 0
            for row in db.execute(f'SELECT id,data FROM {table}').fetchall():
                entry = json.loads(row['data'])
                if entry.get('course_id') == course_id:
                    entry['course_id'] = ''
                    db.execute(f'UPDATE {table} SET data=? WHERE id=?',
                               (json.dumps(entry, ensure_ascii=False), row['id']))
                    detached[table] += 1
        db.execute('DELETE FROM courses WHERE id=?', (course_id,))
    return {'ok': True, 'detached': detached}


class Task(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    course_id: str = ''
    status: str = Field(default='todo', pattern='^(todo|doing|done)$')
    priority: str = Field(default='normal', pattern='^(normal|high)$')
    due: str = ''
    details: str = Field(default='', max_length=10000)
    resource: str = Field(default='', max_length=2000)
    planned_date: str = ''
    planned_time: str = ''
    duration_minutes: int = Field(default=25, ge=1, le=1440)
    remind_at: str = ''

    @field_validator('due', 'remind_at')
    @classmethod
    def valid_due(cls, value):
        if value:
            date = datetime.fromisoformat(value)
            if date.tzinfo is None:
                raise ValueError('Deadline must include a time zone')
        return value

    @field_validator('planned_date')
    @classmethod
    def valid_date(cls, value):
        if value and (not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value) or not date.fromisoformat(value)):
            raise ValueError('Use YYYY-MM-DD')
        return value

    @field_validator('planned_time')
    @classmethod
    def valid_planned_time(cls, value):
        if value:
            clock_time.fromisoformat(value)
            if not re.fullmatch(r'\d{2}:\d{2}', value):
                raise ValueError('Use HH:MM')
        return value

    @field_validator('resource')
    @classmethod
    def valid_resource(cls, value):
        if value and (not value.startswith('/api/') or value.startswith('//')):
            raise ValueError('Only workspace resources are supported')
        return value


class Slot(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    course_id: str = ''
    kind: str = Field(default='study', pattern='^(study|lecture|section|lab)$')
    day: int = Field(ge=0, le=6)
    start: str
    end: str
    location: str = Field(default='', max_length=200)

    @field_validator('start', 'end')
    @classmethod
    def valid_time(cls, value):
        clock_time.fromisoformat(value)
        if not re.fullmatch(r'\d{2}:\d{2}', value):
            raise ValueError('Use HH:MM')
        return value


class Note(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    course_id: str = ''
    content: str = Field(default='', max_length=500000)


def save_entry(table, body, entry_id=None):
    check_course(body.course_id)
    if table == 'slots' and body.end <= body.start:
        raise HTTPException(400, 'نهاية الموعد لازم تكون بعد بدايته')
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute(f'SELECT data FROM {table} WHERE id=?', (entry_id,)).fetchone() if entry_id else None
        if entry_id and not row:
            raise HTTPException(404, 'العنصر غير موجود')
        old = json.loads(row['data']) if row else {'id': uuid.uuid4().hex, 'created': time.time()}
        value = old | body.model_dump(exclude_unset=bool(entry_id)) | {'updated': time.time()}
        if table == 'tasks':
            if value.get('planned_time') and not value.get('planned_date'):
                raise HTTPException(400, 'حدد يوم التنفيذ مع وقت البداية')
            timer = old.get('timer', {})
            if value.get('duration_minutes', 25) != old.get('duration_minutes', 25):
                if timer.get('state') == 'running' and timer.get('ends_at', 0) > time.time():
                    raise HTTPException(409, 'أوقف أو صفّر التايمر قبل تغيير مدته')
                value['timer'] = {'state': 'idle', 'remaining_seconds': value['duration_minutes'] * 60}
            if value['status'] == 'done' and timer.get('state') == 'running':
                timer = log_focus(db, old, timer, time.time())
                value['timer'] = timer | {'state': 'paused', 'remaining_seconds': max(0, math.ceil(timer['ends_at'] - time.time())), 'ends_at': None}
        db.execute(f'INSERT OR REPLACE INTO {table}(id,data) VALUES (?,?)',
                   (value['id'], json.dumps(value, ensure_ascii=False)))
    return value


@app.post('/api/tasks', dependencies=[Depends(authorized)])
def new_task(body: Task):
    if 'duration_minutes' not in body.model_fields_set:
        body = body.model_copy(update={'duration_minutes': setting('default_task_minutes', 25)})
    return save_entry('tasks', body)


@app.put('/api/tasks/{entry_id}', dependencies=[Depends(authorized)])
def update_task(entry_id: str, body: Task):
    return save_entry('tasks', body, entry_id)


class TimerAction(BaseModel):
    action: str = Field(pattern='^(start|pause|reset|configure)$')
    duration_minutes: int | None = Field(default=None, ge=1, le=1440)
    set_default: bool = False


class TaskPreferences(BaseModel):
    duration_minutes: int = Field(ge=1, le=1440)


@app.put('/api/preferences/tasks', dependencies=[Depends(authorized)])
def task_preferences(body: TaskPreferences):
    set_setting('default_task_minutes', body.duration_minutes)
    return body.model_dump()


def log_focus(db, task, timer, until):
    """Record the focus time of the current timer segment (for the weekly review)."""
    start = timer.get('segment_start')
    if timer.get('state') != 'running' or not start:
        return timer
    end = min(until, timer.get('ends_at') or until)
    if end - start >= 30:
        db.execute('INSERT OR REPLACE INTO study_sessions(id,data) VALUES (?,?)', (uuid.uuid4().hex, json.dumps({
            'task_id': task['id'], 'course_id': task.get('course_id', ''), 'start': start, 'end': end,
            'minutes': round((end - start) / 60, 1)}, ensure_ascii=False)))
    return timer | {'segment_start': end}


@app.post('/api/tasks/{entry_id}/timer', dependencies=[Depends(authorized)])
def task_timer(entry_id: str, body: TimerAction):
    now = time.time()
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT data FROM tasks WHERE id=?', (entry_id,)).fetchone()
        if not row:
            raise HTTPException(404, 'المهمة غير موجودة')
        task = json.loads(row['data'])
        timer = task.get('timer', {})
        if timer.get('state') == 'running' and (body.action != 'start' or timer.get('ends_at', 0) <= now):
            timer = log_focus(db, task, timer, now)
        if body.action == 'configure' and body.duration_minutes is None:
            raise HTTPException(400, 'حدد مدة التايمر')
        if body.duration_minutes is not None and body.duration_minutes != task.get('duration_minutes', 25):
            if body.action not in ('configure', 'reset') and timer.get('state') == 'running' and timer.get('ends_at', 0) > now:
                raise HTTPException(409, 'أوقف التايمر أو استخدم تغيير المدة لإعادة ضبط الجلسة')
            task['duration_minutes'] = body.duration_minutes
            timer = {'state': 'idle', 'remaining_seconds': body.duration_minutes * 60}
        if body.set_default:
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',
                       ('default_task_minutes', json.dumps(task.get('duration_minutes', 25))))
        if body.action == 'reset':
            timer = {'state': 'idle', 'remaining_seconds': task.get('duration_minutes', 25) * 60}
        elif body.action == 'start':
            if task['status'] == 'done':
                raise HTTPException(409, 'أعد فتح المهمة قبل بدء التايمر')
            if timer.get('state') != 'running' or timer.get('ends_at', 0) <= now:
                remaining = timer.get('remaining_seconds', 0) if timer.get('state') == 'paused' else 0
                run_id = timer.get('run_id') if remaining > 0 else uuid.uuid4().hex
                remaining = remaining if remaining > 0 else task.get('duration_minutes', 25) * 60
                timer = {'state': 'running', 'remaining_seconds': remaining, 'ends_at': now + remaining, 'run_id': run_id, 'segment_start': now}
                task['status'] = 'doing'
        elif body.action == 'pause' and timer.get('state') == 'running':
            remaining = max(0, math.ceil(timer['ends_at'] - now))
            timer = timer | {'state': 'paused' if remaining else 'finished', 'remaining_seconds': remaining,
                             'finished_at': now if not remaining else None, 'ends_at': None}
        task = task | {'timer': timer, 'updated': now}
        db.execute('UPDATE tasks SET data=? WHERE id=?', (json.dumps(task, ensure_ascii=False), entry_id))
    return task


@app.post('/api/slots', dependencies=[Depends(authorized)])
def new_slot(body: Slot):
    return save_entry('slots', body)


@app.put('/api/slots/{entry_id}', dependencies=[Depends(authorized)])
def update_slot(entry_id: str, body: Slot):
    return save_entry('slots', body, entry_id)


@app.post('/api/notes', dependencies=[Depends(authorized)])
def new_note(body: Note):
    return save_entry('notes', body)


@app.put('/api/notes/{entry_id}', dependencies=[Depends(authorized)])
def update_note(entry_id: str, body: Note):
    return save_entry('notes', body, entry_id)


@app.delete('/api/{table}/{entry_id}', dependencies=[Depends(authorized)])
def delete_entry(table: str, entry_id: str):
    if table not in ('tasks', 'slots', 'notes', 'uploads'):
        raise HTTPException(404)
    entry = get(table, entry_id)
    with database() as db:
        db.execute(f'DELETE FROM {table} WHERE id=?', (entry_id,))
    if table == 'uploads':
        (data_root() / 'uploads' / entry['id']).unlink(missing_ok=True)
    if table == 'tasks':
        with database() as db:
            db.execute('DELETE FROM reminder_deliveries WHERE task_id=?', (entry_id,))
    return {'ok': True}


MATH_INLINE = re.compile(r'(?<![\\$\w])\$(?![\s$])([^\n$]+?)(?<![\s\\])\$(?![\w$])')
MATH_BLOCK = re.compile(r'\$\$(.+?)\$\$', re.S)
CALLOUT = re.compile(r'^\[!([A-Za-z-]+)\]([+-]?)[ \t]*(.*)$')
CALLOUT_ICONS = {'note': 'ℹ', 'info': 'ℹ', 'tip': '✦', 'hint': '✦', 'important': '✦', 'success': '✓', 'check': '✓',
                 'done': '✓', 'question': '?', 'help': '?', 'faq': '?', 'warning': '!', 'caution': '!', 'attention': '!',
                 'danger': '!', 'error': '✕', 'failure': '✕', 'bug': '✕', 'example': '›', 'quote': '“', 'cite': '“',
                 'abstract': '≡', 'summary': '≡', 'tldr': '≡', 'todo': '○'}
DIR_AUTO = {'p', 'li', 'ul', 'ol', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'table'}


def markdown(content, relative=''):
    # Raw HTML is disabled; links/images are resolved only into approved vault roots.
    # Obsidian extras: callouts, task lists, ==highlight==, ~~strike~~, %%comments%%, $math$, ```mermaid.
    from markdown_it.token import Token
    content = content.replace('\r\n', '\n')
    if content.startswith('---\n'):
        parts = content.split('\n---', 1)
        if len(parts) == 2:
            content = parts[1].lstrip('-').lstrip('\n')
    def slug(text):
        return re.sub(r'\s+', '-', re.sub(r'[^\w\s-]', '', text.lower())).strip('-')
    indexed = None
    def resolve(target, image=False):
        nonlocal indexed
        raw, _, anchor = unquote(target).partition('#')
        target = raw
        fragment = '#' + quote(slug(anchor)) if anchor else ''
        if not target:
            return fragment
        root = vault_root()
        options = [str(Path(relative).parent / target), target]
        if not Path(target).suffix:
            options += [p + '.md' for p in options[:]]
        for candidate in options:
            try:
                normalized = (root / candidate).resolve().relative_to(root)
                path = vault_path(str(normalized))
                if path.is_file():
                    endpoint = 'note' if path.suffix.lower() == '.md' and not image else 'file'
                    return '/api/vault/' + endpoint + '?path=' + quote(str(path.relative_to(root))) + fragment
            except (HTTPException, ValueError):
                pass
        if indexed is None:
            indexed = vault_files()
        matches = [f for f in indexed if f['name'] in (target, target + '.md') or f['path'].endswith('/' + target)]
        if len(matches) == 1:
            f = matches[0]
            endpoint = 'note' if f['kind'] == 'note' and not image else 'file'
            return '/api/vault/' + endpoint + '?path=' + quote(f['path']) + fragment
        return ''
    def wiki(match):
        embedded, target, label = match.group(1), match.group(2), match.group(3)
        is_image = bool(embedded) and Path(target.split('#', 1)[0]).suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.svg'}
        url = resolve(target, is_image)
        label = (label or Path(target.split('#', 1)[0]).stem or target).replace('[', '').replace(']', '')
        if not url:
            return label + ' (غير موجود في الفولدرات المربوطة)'
        return ('!' if is_image else '') + '[' + label + '](' + url + ')'
    maths = []
    def stash(display):
        def keep(match):
            maths.append((display, match.group(1).strip()))
            return '@@MATH' + str(len(maths) - 1) + '@@'
        return keep
    # Do not rewrite code blocks.
    segments = re.split(r'(```.*?```|~~~.*?~~~|`[^`\n]*`)', content, flags=re.S)
    for i in range(0, len(segments), 2):
        text = re.sub(r'%%.*?%%', '', segments[i], flags=re.S)
        text = MATH_BLOCK.sub(stash(True), text)
        text = MATH_INLINE.sub(stash(False), text)
        segments[i] = re.sub(r'(!?)\[\[([^\]|]+)(?:\|([^\]]+))?\]\]', wiki, text)
    renderer = MarkdownIt('commonmark', {'html': False, 'linkify': False}).enable(['table', 'strikethrough'])
    rules = renderer.renderer.rules
    original_image = rules.get('image')
    def image_rule(tokens, idx, options, env):
        token = tokens[idx]
        src = token.attrGet('src') or ''
        if not src.startswith('/api/vault/file?'):
            token.attrSet('src', resolve(src, True) or '/missing-attachment')
        token.attrSet('loading', 'lazy')
        return original_image(tokens, idx, options, env)
    def link_rule(tokens, idx, options, env):
        token = tokens[idx]
        href = token.attrGet('href') or ''
        if href and not href.startswith(('#', '/api/')) and not urlparse(href).scheme:
            resolved = resolve(href)
            if resolved:
                token.attrSet('href', resolved)
        elif urlparse(href).scheme in ('http', 'https'):
            token.attrSet('target', '_blank')
            token.attrSet('rel', 'noopener')
        return renderer.renderer.renderToken(tokens, idx, options, env)
    def heading_rule(tokens, idx, options, env):
        if idx + 1 < len(tokens):
            tokens[idx].attrSet('id', slug(tokens[idx + 1].content))
        return renderer.renderer.renderToken(tokens, idx, options, env)
    def text_rule(tokens, idx, options, env):
        escaped = html.escape(tokens[idx].content, quote=False)
        return re.sub(r'==(?=\S)(.+?)(?<=\S)==', r'<mark>\1</mark>', escaped)
    original_fence = rules.get('fence')
    def fence_rule(tokens, idx, options, env):
        token = tokens[idx]
        language = (token.info or '').strip().split(' ')[0].lower()
        if language == 'mermaid':
            return '<div class="mermaid-wrap" dir="ltr"><pre class="mermaid">' + html.escape(token.content) + '</pre></div>\n'
        return '<div class="code-block" dir="ltr">' + original_fence(tokens, idx, options, env) + '</div>\n'
    rules['image'] = image_rule
    rules['link_open'] = link_rule
    rules['heading_open'] = heading_rule
    rules['text'] = text_rule
    rules['fence'] = fence_rule

    def obsidian(state):
        tokens = state.tokens
        for i, token in enumerate(tokens):
            if token.nesting == 1 and token.tag in DIR_AUTO:
                token.attrSet('dir', 'auto')
            # Task lists: "- [ ] item" / "- [x] item"
            if token.type == 'list_item_open' and i + 2 < len(tokens) and tokens[i + 2].type == 'inline':
                inline = tokens[i + 2]
                found = re.match(r'^\[([ xX])\][ \t]+', inline.content)
                if found and inline.children and inline.children[0].type == 'text':
                    first = inline.children[0]
                    first.content = first.content[found.end():]
                    box = Token('html_inline', '', 0)
                    box.content = '<span class="task-box' + (' done' if found.group(1) != ' ' else '') + '" aria-hidden="true"></span>'
                    inline.children.insert(0, box)
                    token.attrSet('class', 'task-item' + (' done' if found.group(1) != ' ' else ''))
            # Callouts: "> [!note] Title"
            if token.type == 'blockquote_open' and i + 2 < len(tokens) and tokens[i + 2].type == 'inline':
                inline = tokens[i + 2]
                first_line = inline.content.split('\n', 1)[0]
                found = CALLOUT.match(first_line)
                if not found:
                    continue
                kind = found.group(1).lower()
                fold = found.group(2)
                title = found.group(3).strip() or kind.capitalize()
                token.tag = 'div'
                token.attrSet('class', 'callout callout-' + kind + (' foldable' if fold else '') + (' folded' if fold == '-' else ''))
                for close in tokens[i + 1:]:
                    if close.type == 'blockquote_close' and close.level == token.level:
                        close.tag = 'div'
                        break
                children = inline.children or []
                cut = next((n for n, child in enumerate(children) if child.type in ('softbreak', 'hardbreak')), len(children))
                inline.children = children[cut + 1:]
                inline.content = inline.content.split('\n', 1)[1] if '\n' in inline.content else ''
                head = Token('html_block', '', 0)
                head.content = ('<div class="callout-title" dir="auto"><span class="callout-icon" aria-hidden="true">' +
                                CALLOUT_ICONS.get(kind, 'ℹ') + '</span>' + html.escape(title) + '</div>')
                tokens[i + 1].attrSet('data-callout-body', '1')
                if not inline.children:
                    tokens[i + 1].hidden = True
                    tokens[i + 3].hidden = True
                tokens.insert(i + 1, head)
    renderer.core.ruler.push('obsidian', obsidian)
    rendered = renderer.render(''.join(segments))
    def unstash(match):
        display, tex = maths[int(match.group(1))]
        tex = html.escape(tex)
        if display:
            return '<div class="math math-display" dir="ltr">\\[' + tex + '\\]</div>'
        return '<span class="math math-inline" dir="ltr">\\(' + tex + '\\)</span>'
    rendered = re.sub(r'<p dir="auto">@@MATH(\d+)@@</p>', unstash, rendered)
    return re.sub(r'@@MATH(\d+)@@', unstash, rendered)


@app.get('/api/vault', dependencies=[Depends(authorized)])
def library(course_id: str = ''):
    course = get('courses', course_id) if course_id else {}
    if course_id and not course.get('vault_folder'):
        return {'files': []}
    return {'files': vault_files(course.get('vault_folder'))}


def vault_note_file(path):
    file = vault_path(path)
    if not file.is_file() or file.suffix.lower() != '.md' or file.stat().st_size > 2_000_000:
        raise HTTPException(404)
    return file


@app.get('/api/vault/note', dependencies=[Depends(authorized)], response_class=HTMLResponse)
def vault_note(path: str):
    # Standalone reader (shared links / wiki links). The app itself uses /api/vault/note-data.
    file = vault_note_file(path)
    body = markdown(file.read_text(encoding='utf-8', errors='replace'), path)
    return HTMLResponse('<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8">'
                        '<meta name="viewport" content="width=device-width, initial-scale=1">'
                        '<link rel="stylesheet" href="/static/app.css"><title>' + html.escape(file.stem) +
                        '</title><body class="reader-page"><a class="back" href="/">← مساحتي الدراسية</a>'
                        '<h1 class="reader-title" dir="auto">' + html.escape(file.stem) + '</h1>'
                        '<article class="prose" data-reader>' + body + '</article>'
                        '<script src="/static/reader.js" defer></script></body></html>')


@app.get('/api/vault/note-data', dependencies=[Depends(authorized)])
def vault_note_data(path: str):
    file = vault_note_file(path)
    relative = file.relative_to(vault_root())
    return {'title': file.stem, 'path': str(relative), 'folder': str(relative.parent),
            'modified': file.stat().st_mtime, 'size': file.stat().st_size,
            'html': markdown(file.read_text(encoding='utf-8', errors='replace'), path)}


def serve_document(path, name):
    suffix = Path(name).suffix.lower()
    safe_inline = {'.pdf', '.png', '.jpg', '.jpeg', '.webp', '.gif', '.mp3', '.mp4', '.wav'}
    return FileResponse(path, filename=name, media_type=mimetypes.guess_type(name)[0] or 'application/octet-stream',
                        content_disposition_type='inline' if suffix in safe_inline else 'attachment')


@app.get('/api/vault/file', dependencies=[Depends(authorized)])
def vault_file(path: str):
    file = vault_path(path)
    if not file.is_file() or file.suffix.lower() not in DOCUMENTS:
        raise HTTPException(404)
    return serve_document(file, file.name)


@app.get('/api/notes/{entry_id}', dependencies=[Depends(authorized)])
def note(entry_id: str):
    item = get('notes', entry_id)
    return item | {'html': markdown(item['content'])}


@app.post('/api/uploads', dependencies=[Depends(authorized)])
async def upload(request: Request, name: str, course_id: str = ''):
    check_course(course_id)
    name = Path(name.replace('\\', '/')).name[:240]
    if not name or name.startswith('.'):
        raise HTTPException(400, 'اسم الملف غير صالح')
    from . import accounts
    left = accounts.remaining_bytes()
    maximum = 512 * 1024 * 1024 if left is None else min(512 * 1024 * 1024, left)
    folder = data_root() / 'uploads'
    folder.mkdir(exist_ok=True, mode=0o700)
    key = uuid.uuid4().hex
    file = folder / key
    size = 0
    try:
        with file.open('xb') as stream:
            async for chunk in request.stream():
                size += len(chunk)
                if size > maximum:
                    raise HTTPException(413, 'الحد الأقصى للملف 512 ميجابايت' if left is None or maximum == 512 * 1024 * 1024 else 'الملف أكبر من المساحة الفاضية عندك على السيرفر')
                stream.write(chunk)
        return put('uploads', {'id': key, 'name': name, 'size': size, 'course_id': course_id, 'created': time.time()})
    except BaseException:
        file.unlink(missing_ok=True)
        raise


@app.get('/api/uploads/{entry_id}', dependencies=[Depends(authorized)])
def uploaded_file(entry_id: str):
    entry = get('uploads', entry_id)
    return serve_document(data_root() / 'uploads' / entry_id, entry['name'])


def archive_root():
    return Path(os.environ.get('ARCHIVE_DATA_DIR', 'data')).resolve()


def archive_rows(query, args=()):
    file = archive_root() / 'archive.sqlite3'
    if not file.is_file():
        return []
    with closing(sqlite3.connect(file.as_uri() + '?mode=ro', uri=True, timeout=20)) as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute(query, args)]


@app.get('/api/archive', dependencies=[Depends(authorized)])
def archive():
    accounts = [json.loads(r['meta']) | {'synced_at': r['synced_at']} for r in archive_rows('SELECT meta,synced_at FROM accounts ORDER BY key')]
    courses = [json.loads(r['data']) | {'account': r['account']} for r in archive_rows('SELECT account,data FROM courses ORDER BY account,id')]
    return {'accounts': accounts, 'courses': courses}


def archive_items(account, course):
    items = [json.loads(r['data']) for r in archive_rows('SELECT data FROM items WHERE account=? AND active=1', (account,))]
    output = []
    for item in items:
        if item.get('course_id') != course or item.get('restricted'):
            continue
        files = []
        for i, link in enumerate(item.get('links', [])):
            f = link.get('file')
            if f:
                files.append({'name': f.get('name', item['title']), 'size': f.get('size', 0),
                              'url': '/api/archive/file/' + quote(account, safe='') + '/' + quote(item['id'], safe='') + '/' + str(i)})
        output.append({'id': item['id'], 'title': item['title'], 'category': item.get('category', ''), 'files': files})
    return output


@app.get('/api/archive/{account}/{course}/items', dependencies=[Depends(authorized)])
def list_archive_items(account: str, course: str):
    return {'items': archive_items(account, course)}


@app.get('/api/archive/file/{account}/{item_id}/{index}', dependencies=[Depends(authorized)])
def archive_file(account: str, item_id: str, index: int):
    records = archive_rows('SELECT data FROM items WHERE account=? AND id=? AND active=1', (account, item_id))
    if not records:
        raise HTTPException(404)
    item = json.loads(records[0]['data'])
    if item.get('restricted') or index < 0 or index >= len(item.get('links', [])):
        raise HTTPException(404)
    f = item['links'][index].get('file')
    if not f:
        raise HTTPException(404)
    path = (archive_root() / f['path']).resolve()
    if not path.is_relative_to(archive_root()) or not path.is_file():
        raise HTTPException(404)
    return serve_document(path, f['name'])


class ImportCourse(BaseModel):
    account: str
    course: str
    existing_id: str = ''


@app.post('/api/archive/import', dependencies=[Depends(authorized)])
def import_course(body: ImportCourse):
    records = archive_rows('SELECT data FROM courses WHERE account=? AND id=?', (body.account, body.course))
    if not records:
        raise HTTPException(404, 'المادة غير موجودة في الأرشيف')
    source = json.loads(records[0]['data'])
    for c in rows('courses'):
        if c.get('archive_account') == body.account and c.get('archive_course') == body.course:
            return c
    old = get('courses', body.existing_id) if body.existing_id else {
        'id': uuid.uuid4().hex, 'title': source['title'], 'vault_folder': '', 'created': time.time()}
    return put('courses', old | {'kind': 'college', 'archive_account': body.account, 'archive_course': body.course})


@app.get('/api/state', dependencies=[Depends(authorized)])
def state():
    root = vault_root()
    files = vault_files()
    folders = sorted({f['folder'] for f in files} | {m for m in mounts() if (root / m).is_dir()})
    available = sorted(p.name for p in root.iterdir() if p.is_dir() and visible_parts(Path(p.name)) and not p.is_symlink()) if root.is_dir() else []
    return {'courses': rows('courses'), 'tasks': rows('tasks'), 'slots': rows('slots'),
            'notes': [{k: v for k, v in n.items() if k != 'content'} for n in rows('notes')],
            'uploads': rows('uploads'), 'mounts': mounts(), 'vault_folders': folders,
            'available_folders': available, 'vault_count': len(files), 'vault_notes': sum(f['kind'] == 'note' for f in files),
            'vault_available': bool(files), 'timezone': 'Africa/Cairo', 'server_time': time.time(),
            'task_defaults': {'duration_minutes': setting('default_task_minutes', 25)},
            'user': current_user_public()}


def current_user_public():
    from . import accounts
    user = accounts.CURRENT_USER.get()
    return accounts.public(user) if user else {'name': os.environ.get('STUDY_OWNER_NAME', ''), 'username': ''}


@app.get('/health')
def health():
    return {'status': 'ok', 'service': 'studyhub'}


@app.get('/')
def home():
    return FileResponse(STATIC / 'index.html')


@app.get('/sw.js')
def service_worker():
    return FileResponse(STATIC / 'sw.js', media_type='application/javascript', headers={'Service-Worker-Allowed': '/'})


@app.get('/manifest.webmanifest')
def manifest():
    return FileResponse(STATIC / 'manifest.webmanifest', media_type='application/manifest+json')


app.mount('/static', StaticFiles(directory=STATIC), name='static')

from .guard import RequestGuard  # noqa: E402
app.add_middleware(RequestGuard)

from .reminders import router as reminders_router
app.include_router(reminders_router)


from .mcp_server import router as mcp_router  # noqa: E402  (MCP endpoint for Claude)
app.include_router(mcp_router)
from .assistant import router as assistant_router  # noqa: E402  (in-app AI chat)
app.include_router(assistant_router)
from .voices import router as voices_router  # noqa: E402  (deadline voice reminders)
app.include_router(voices_router)
from .vault_upload import router as vault_upload_router, account as account_router  # noqa: E402  (multi-user site)
app.include_router(vault_upload_router)
app.include_router(account_router)


@app.get('/api/weekly', dependencies=[Depends(authorized)])
def weekly(weeks_ago: int = 0):
    from .insights import weekly_report
    return weekly_report(max(0, min(weeks_ago, 104)))
