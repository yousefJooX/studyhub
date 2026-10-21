"""Shared fixtures for the StudyHub test suite.

Run from the folder that CONTAINS the studyhub package:
    cd ~/studyhub && python -m pytest studyhub/tests -q
Every test gets its own empty data folder, so tests never touch real data.
"""
import hashlib
import json
import os
import secrets
import sqlite3
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]          # folder that contains the studyhub package
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
FIXTURES = Path(__file__).parent / 'fixtures'
OWNER_PASSWORD = 'owner-password-123'

os.environ.setdefault('STUDY_REMINDERS_ENABLED', 'false')
os.environ.setdefault('STUDY_DATA_DIR', '/tmp/studyhub-tests-import')

from fastapi.testclient import TestClient  # noqa: E402

from studyhub.app import app  # noqa: E402

BASE_ENV = {'STUDY_SECURE_COOKIE': 'false', 'STUDY_REMINDERS_ENABLED': 'false', 'STUDY_MIN_FREE_MB': '1',
            'STUDY_RATE_LIMIT': '100000', 'STUDY_BOARD_SYNC': '1'}
CLEAR = ['STUDY_MULTIUSER', 'STUDY_PASSWORD_HASH', 'STUDY_API_TOKEN_HASH', 'STUDY_SIGNUP', 'STUDY_SIGNUP_CODE',
         'STUDY_COLLEGE_CODE', 'STUDY_LLM_API_KEY', 'STUDY_LLM_BASE_URL', 'STUDY_LLM_MODEL', 'STUDY_LLM_FALLBACK_BASE_URL',
         'STUDY_LLM_FALLBACK_API_KEY', 'STUDY_LLM_FALLBACK_MODEL', 'STUDY_PROVIDER_BASE_GROQ', 'STUDY_BOARD_URL',
         'ARCHIVE_DATA_DIR', 'STUDY_VAULT_ROOT', 'STUDY_PUBLIC_ORIGIN', 'STUDY_USER_QUOTA_MB', 'STUDY_ASSISTANT_DAILY',
         'STUDY_OWNER_NAME', 'STUDY_MAX_USERS']


def password_hash(password):
    salt = secrets.token_bytes(16)
    return salt.hex() + ':' + hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 600_000).hex()


_OWNER_HASH = password_hash(OWNER_PASSWORD)


@pytest.fixture
def env(monkeypatch, tmp_path):
    for key in CLEAR:
        monkeypatch.delenv(key, raising=False)
    for key, value in BASE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv('STUDY_DATA_DIR', str(tmp_path / 'data'))
    (tmp_path / 'data').mkdir()
    yield monkeypatch


@pytest.fixture
def personal(env):
    """Logged-in client of the single-owner site."""
    env.setenv('STUDY_PASSWORD_HASH', _OWNER_HASH)
    client = TestClient(app)
    response = client.post('/api/login', json={'password': OWNER_PASSWORD})
    assert response.status_code == 200, response.text
    return client


class Users:
    """Creates logged-in clients on the friends (multi-user) site."""

    def __init__(self):
        self.count = 0

    def __call__(self, name=None, college_code='', level=None, program='', **extra):
        self.count += 1
        name = name or f'user{self.count}'
        client = TestClient(app)
        body = {'name': name.title(), 'username': name, 'password': 'goodpass99', **extra}
        if college_code:
            body.update(college_code=college_code, level=level, program=program)
        response = client.post('/api/signup', json=body, headers={'x-forwarded-for': f'10.1.{self.count}.1'})
        assert response.status_code == 200, response.text
        client.headers['x-forwarded-for'] = f'10.1.{self.count}.1'
        return client


@pytest.fixture
def users(env):
    env.setenv('STUDY_MULTIUSER', '1')
    env.setenv('STUDY_COLLEGE_CODE', 'cs')
    return Users()


# ---------- tiny local HTTP servers for things StudyHub calls out to

def _serve(handler):
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def fake_board(env):
    """A stand-in for HNU Board built from real captured pages. Deadlines are relative to now:
    posts 1, 2, 8 are open; 3 and 7 are closed."""
    offsets = {'1': timedelta(days=2), '2': timedelta(days=5), '8': timedelta(days=6),
               '3': timedelta(days=-1), '7': timedelta(days=-4)}
    board = FIXTURES / 'board'

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path, _, query = self.path.partition('?')
            if path == '/':
                params = dict(p.split('=', 1) for p in query.split('&') if '=' in p)
                if params.get('type') == 'assignment':
                    page = 'assignments-1.html' if params.get('page', '1') == '1' else None
                else:
                    page = 'main.html'
                body = (board / page).read_bytes() if page else b'<html></html>'
                return self._send(200, body, 'text/html; charset=utf-8')
            if path.startswith('/post/') and path.endswith('/calendar.ics'):
                post = path.split('/')[2]
                if post not in offsets:
                    return self._send(404, b'not found', 'text/plain')
                start = (datetime.now(timezone.utc) + offsets[post]).strftime('%Y%m%dT%H%M%SZ')
                return self._send(200, f'BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nDTSTART:{start}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n'.encode(), 'text/calendar')
            self._send(404, b'not found', 'text/plain')

        def _send(self, code, body, ctype):
            self.send_response(code); self.send_header('Content-Type', ctype); self.end_headers(); self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = _serve(Handler)
    env.setenv('STUDY_BOARD_URL', f'http://127.0.0.1:{server.server_port}')
    yield server
    server.shutdown()


@pytest.fixture
def fake_llm(env):
    """OpenAI-compatible provider. Accepts keys starting with gsk_good. Scripted tool calls."""
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def _ok(self):
            auth = self.headers.get('Authorization', '')
            seen.append(auth)
            return auth.startswith('Bearer gsk_good')

        def do_GET(self):
            if not self._ok():
                return self._send(401, {'error': {'message': 'bad key'}})
            self._send(200, {'data': [{'id': 'openai/gpt-oss-120b'}, {'id': 'other'}]})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if not self._ok():
                return self._send(401, {'error': {'message': 'bad key'}})
            if body['messages'][-1]['role'] == 'user':
                message = {'role': 'assistant', 'content': None, 'tool_calls': [
                    {'id': 'c1', 'type': 'function', 'function': {'name': 'create_course', 'arguments': json.dumps({'title': 'Robotics'})}},
                    {'id': 'c2', 'type': 'function', 'function': {'name': 'create_task', 'arguments': json.dumps(
                        {'title': 'Lab report', 'course': 'robotics', 'due': '2030-01-02T23:59', 'priority': 'high'})}},
                    {'id': 'c3', 'type': 'function', 'function': {'name': 'create_slot', 'arguments': json.dumps(
                        {'title': 'Robotics lab', 'course': 'Robotics', 'day': 'Monday', 'start': '12:00', 'end': '14:00', 'kind': 'lab'})}}]}
            else:
                message = {'role': 'assistant', 'content': 'Done'}
            self._send(200, {'choices': [{'message': message}]})

        def _send(self, code, data):
            out = json.dumps(data).encode()
            self.send_response(code); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(out)

        def log_message(self, *args):
            pass

    server = _serve(Handler)
    env.setenv('STUDY_PROVIDER_BASE_GROQ', f'http://127.0.0.1:{server.server_port}')
    server.seen = seen
    server.base = f'http://127.0.0.1:{server.server_port}'
    yield server
    server.shutdown()


@pytest.fixture
def archive(env, tmp_path):
    """A small college archive database with one course and one lecture file."""
    root = tmp_path / 'archive'
    (root / 'files').mkdir(parents=True)
    (root / 'files' / 'l1.pdf').write_bytes(b'%PDF-1.4 lecture')
    db = sqlite3.connect(root / 'archive.sqlite3')
    db.executescript('''CREATE TABLE accounts(key TEXT, meta TEXT, synced_at REAL);
        CREATE TABLE courses(account TEXT, id TEXT, data TEXT);
        CREATE TABLE items(account TEXT, id TEXT, data TEXT, active INTEGER);''')
    db.execute('INSERT INTO accounts VALUES (?,?,?)', ('y4', json.dumps({'key': 'y4', 'year': 4, 'department': 'Robotics', 'term': 1}), 0))
    db.execute('INSERT INTO courses VALUES (?,?,?)', ('y4', 'c1', json.dumps({'id': 'c1', 'title': 'Robot Design'})))
    db.execute('INSERT INTO items VALUES (?,?,?,1)', ('y4', 'i1', json.dumps(
        {'id': 'i1', 'course_id': 'c1', 'title': 'Lecture 1', 'links': [{'file': {'path': 'files/l1.pdf', 'name': 'l1.pdf'}}]})))
    db.commit()
    db.close()
    env.setenv('ARCHIVE_DATA_DIR', str(root))
    return root
