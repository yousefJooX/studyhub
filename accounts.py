"""Accounts for the multi-user StudyHub site (STUDY_MULTIUSER=1).

Every user gets a private folder <STUDY_DATA_DIR>/users/<id>/ with their own study.sqlite3,
uploads, Obsidian vault copy and voice clips, so all existing StudyHub code works unchanged
once the request's user is known. Only accounts, sessions and API tokens live in the shared
accounts.sqlite3. The college archive stays shared and read-only.
"""
import contextvars
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from fastapi import HTTPException, Request, Response
from pydantic import BaseModel, Field

CURRENT_USER = contextvars.ContextVar('studyhub_user', default=None)
USERNAME = re.compile(r'^[a-z0-9_.-]{3,32}$')
SESSION_DAYS = 30


def enabled():
    return os.environ.get('STUDY_MULTIUSER') == '1'


def base_root():
    root = Path(os.environ.get('STUDY_DATA_DIR', 'data/studyhub')).resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def user_root(user_id):
    if not re.fullmatch(r'[0-9a-f]{32}', user_id or ''):
        raise HTTPException(401, 'سجّل الدخول أولًا')
    root = base_root() / 'users' / user_id
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


@contextmanager
def accounts_db():
    db = sqlite3.connect(base_root() / 'accounts.sqlite3', timeout=20)
    db.row_factory = sqlite3.Row
    try:
        with db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
                password TEXT NOT NULL, created REAL NOT NULL, disabled INTEGER NOT NULL DEFAULT 0, last_seen REAL);
            CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS api_tokens(token TEXT PRIMARY KEY, user_id TEXT NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS attempts(key TEXT PRIMARY KEY, started REAL NOT NULL, count INTEGER NOT NULL);
            ''')
            yield db
    finally:
        db.close()


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def hash_password(password):
    salt = secrets.token_bytes(16)
    return salt.hex() + ':' + hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 310_000).hex()


def check_password(password, encoded):
    try:
        salt, expected = encoded.split(':', 1)
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 310_000).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def public(row):
    return {'id': row['id'], 'username': row['username'], 'name': row['name']}


def user_by(column, value):
    with accounts_db() as db:
        row = db.execute(f'SELECT * FROM users WHERE {column}=?', (value,)).fetchone()
    return dict(row) if row and not row['disabled'] else None


def resolve(request: Request):
    """Find the user for this request from the session cookie, a Bearer API token or /mcp/<token>."""
    header = request.headers.get('authorization', '')
    token = header[7:].strip() if header.lower().startswith('bearer ') else ''
    path = request.url.path
    if not token and path.startswith('/mcp/'):
        token = path.split('/', 2)[2]
    now = time.time()
    with accounts_db() as db:
        if token:
            row = db.execute('SELECT user_id FROM api_tokens WHERE token=?', (digest(token),)).fetchone()
        else:
            cookie = request.cookies.get('study_session', '')
            row = db.execute('SELECT user_id FROM sessions WHERE token=? AND expires>?', (digest(cookie), now)).fetchone() if cookie else None
        if not row:
            return None
        user = db.execute('SELECT * FROM users WHERE id=? AND disabled=0', (row['user_id'],)).fetchone()
        if user and (user['last_seen'] or 0) < now - 300:
            db.execute('UPDATE users SET last_seen=? WHERE id=?', (now, user['id']))
    return dict(user) if user else None


def client_ip(request: Request):
    """Real client address. Behind the local tunnel every request comes from 127.0.0.1, so take the
    address the proxy appended LAST to X-Forwarded-For (earlier entries can be forged by the client)."""
    host = request.client.host if request.client else 'unknown'
    if host in ('127.0.0.1', '::1', 'localhost'):
        hops = [h.strip() for h in request.headers.get('x-forwarded-for', '').split(',') if h.strip()]
        if hops:
            return hops[-1][:64]
    return host


def disk_guard(extra=0):
    """Refuse new uploads when the server disk is almost full (STUDY_MIN_FREE_MB, default 2048)."""
    import shutil
    free = shutil.disk_usage(base_root()).free
    if free - extra < int(os.environ.get('STUDY_MIN_FREE_MB', '2048')) * 1024 * 1024:
        raise HTTPException(507, 'مساحة السيرفر قربت تخلص، الرفع متوقف مؤقتًا. كلّم صاحب الموقع')


def throttle(key, limit, window):
    now = time.time()
    with accounts_db() as db:
        row = db.execute('SELECT started,count FROM attempts WHERE key=?', (key,)).fetchone()
        count = row['count'] if row and row['started'] > now - window else 0
        if count >= limit:
            raise HTTPException(429, 'محاولات كتير. استنى شوية وجرب تاني')
        db.execute('INSERT OR REPLACE INTO attempts VALUES (?,?,?)', (key, row['started'] if count else now, count + 1))


def start_session(response: Response, user_id):
    token = secrets.token_urlsafe(32)
    now = time.time()
    with accounts_db() as db:
        db.execute('DELETE FROM sessions WHERE expires<?', (now,))
        db.execute('INSERT INTO sessions VALUES (?,?,?)', (digest(token), user_id, now + SESSION_DAYS * 86400))
    response.set_cookie('study_session', token, max_age=SESSION_DAYS * 86400, httponly=True,
                        secure=os.environ.get('STUDY_SECURE_COOKIE', 'true') == 'true', samesite='strict')


class Signup(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    name: str = Field(min_length=1, max_length=60)
    password: str = Field(min_length=8, max_length=256)
    code: str = Field(default='', max_length=200)


class AccountLogin(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=512)


def signup_open():
    return enabled() and os.environ.get('STUDY_SIGNUP', 'open') == 'open'


def invite_required():
    """Set STUDY_SIGNUP_CODE to make sign-up need an invite code you share with friends only."""
    return bool(os.environ.get('STUDY_SIGNUP_CODE', ''))


def signup(body: Signup, request: Request, response: Response):
    if not signup_open():
        raise HTTPException(403, 'التسجيل مقفول حاليًا')
    throttle('signup:' + client_ip(request), 5, 3600)
    throttle('signup:all', 30, 3600)  # also cap total sign-ups per hour, whatever the address
    if invite_required() and not hmac.compare_digest(body.code.strip().encode(), os.environ['STUDY_SIGNUP_CODE'].encode()):
        raise HTTPException(403, 'كود الدعوة غلط. اطلبه من صاحب الموقع')
    username = body.username.strip().lower()
    if not USERNAME.fullmatch(username):
        raise HTTPException(400, 'اسم المستخدم من ٣ لـ ٣٢ حرف إنجليزي صغير أو أرقام أو . _ -')
    if body.password.lower() in (username, '12345678', 'password', '123456789'):
        raise HTTPException(400, 'اختار باسورد أصعب من كده')
    maximum = int(os.environ.get('STUDY_MAX_USERS', '100'))
    user_id = uuid.uuid4().hex
    with accounts_db() as db:
        if db.execute('SELECT COUNT(*) FROM users').fetchone()[0] >= maximum:
            raise HTTPException(403, 'الموقع وصل للعدد الأقصى من الحسابات')
        if db.execute('SELECT 1 FROM users WHERE username=?', (username,)).fetchone():
            raise HTTPException(409, 'اسم المستخدم ده محجوز، جرّب اسم تاني')
        db.execute('INSERT INTO users(id,username,name,password,created) VALUES (?,?,?,?,?)',
                   (user_id, username, body.name.strip(), hash_password(body.password), time.time()))
    user_root(user_id)
    start_session(response, user_id)
    return {'authenticated': True, 'user': {'id': user_id, 'username': username, 'name': body.name.strip()}}


def login(body: AccountLogin, request: Request, response: Response):
    username = body.username.strip().lower()
    throttle('login:' + client_ip(request), 15, 900)
    throttle('login-user:' + username, 10, 900)
    with accounts_db() as db:
        row = db.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
    if not row or row['disabled'] or not check_password(body.password, row['password']):
        raise HTTPException(401, 'اسم المستخدم أو الباسورد غلط')
    with accounts_db() as db:
        db.execute('DELETE FROM attempts WHERE key=?', ('login-user:' + username,))
    start_session(response, row['id'])
    return {'authenticated': True, 'user': public(row)}


def logout(request: Request, response: Response):
    with accounts_db() as db:
        db.execute('DELETE FROM sessions WHERE token=?', (digest(request.cookies.get('study_session', '')),))
    response.delete_cookie('study_session')
    return {'ok': True}


def new_api_token(user_id):
    """One API/MCP token per user: creating a new one revokes the old one."""
    token = secrets.token_urlsafe(32)
    with accounts_db() as db:
        db.execute('DELETE FROM api_tokens WHERE user_id=?', (user_id,))
        db.execute('INSERT INTO api_tokens VALUES (?,?,?)', (digest(token), user_id, time.time()))
    return token


def has_api_token(user_id):
    with accounts_db() as db:
        row = db.execute('SELECT created FROM api_tokens WHERE user_id=?', (user_id,)).fetchone()
    return row['created'] if row else None


def revoke_api_token(user_id):
    with accounts_db() as db:
        db.execute('DELETE FROM api_tokens WHERE user_id=?', (user_id,))


def all_users():
    if not (base_root() / 'accounts.sqlite3').exists():
        return []
    with accounts_db() as db:
        return [dict(r) for r in db.execute('SELECT * FROM users WHERE disabled=0')]


def folder_size(path):
    total = 0
    for directory, _, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(directory, name))
            except OSError:
                pass
    return total


def check_quota(extra=0):
    """Raise 413 when the current user's folder would go over STUDY_USER_QUOTA_MB (multi-user only)."""
    user = CURRENT_USER.get()
    if not enabled() or not user:
        return
    quota = int(os.environ.get('STUDY_USER_QUOTA_MB', '1024')) * 1024 * 1024
    disk_guard(extra)
    if folder_size(user_root(user['id'])) + extra > quota:
        raise HTTPException(413, f'مساحتك على السيرفر خلصت ({quota // 1048576} ميجا). امسح ملفات قديمة الأول')


def remaining_bytes():
    """Bytes the current user may still store, or None when there is no quota (single-owner site)."""
    user = CURRENT_USER.get()
    if not enabled() or not user:
        return None
    quota = int(os.environ.get('STUDY_USER_QUOTA_MB', '1024')) * 1024 * 1024
    left = quota - folder_size(user_root(user['id']))
    disk_guard()
    if left <= 0:
        raise HTTPException(413, f'مساحتك على السيرفر خلصت ({quota // 1048576} ميجا). امسح ملفات قديمة الأول')
    return left
