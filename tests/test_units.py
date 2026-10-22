"""Unit tests for every function the API and browser suites did not reach before.
One test (or a few) per function, each in an empty data folder, with no network:
outgoing calls (web push, model providers) are replaced by local fakes."""
import asyncio
import base64
import hashlib
import io
import json
import logging
import os
import stat
import time
import urllib.error
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from .conftest import _serve

CAIRO = ZoneInfo('Africa/Cairo')


def push_keys():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    point = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    b64 = lambda raw: base64.urlsafe_b64encode(raw).decode().rstrip('=')
    return {'p256dh': b64(point), 'auth': b64(os.urandom(16))}


def subscribe(client, endpoint='https://fcm.googleapis.com/fcm/send/unit'):
    body = {'endpoint': endpoint, 'keys': push_keys()}
    assert client.post('/api/notifications/push/subscription', json=body).status_code == 200
    return body


# ---------------------------------------------------------------- __main__ (CLI)

def test_load_config_sets_missing_env_only(env, tmp_path):
    from studyhub.__main__ import load_config
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'STUDY_UNIT_A': 'from-file', 'STUDY_UNIT_B': 5}))
    env.setenv('STUDY_CONFIG_FILE', str(config))
    env.setenv('STUDY_UNIT_A', 'already-set')
    env.delenv('STUDY_UNIT_B', raising=False)
    assert load_config() == config
    assert os.environ['STUDY_UNIT_A'] == 'already-set'      # the environment wins over the file
    assert os.environ['STUDY_UNIT_B'] == '5'
    env.delenv('STUDY_UNIT_B')
    env.setenv('STUDY_CONFIG_FILE', str(tmp_path / 'missing.json'))
    assert load_config() == tmp_path / 'missing.json'       # a missing file is fine


def test_cli_password_hash_is_salted_pbkdf2():
    from studyhub.__main__ import password_hash
    first, second = password_hash('correct horse'), password_hash('correct horse')
    assert first != second                                  # new salt every time
    salt, digest = first.split(':')
    assert len(bytes.fromhex(salt)) == 16
    assert digest == hashlib.pbkdf2_hmac('sha256', b'correct horse', bytes.fromhex(salt), 600_000).hex()
    assert 'correct horse' not in first


def test_seed_vault_adds_each_folder_once(env, tmp_path, capsys):
    from studyhub.__main__ import seed_vault
    from studyhub.app import rows
    vault = tmp_path / 'vault'
    for name in ('ROB 306', 'NLP', '.obsidian'):
        (vault / '1 Univ' / name).mkdir(parents=True)
    (vault / '1 Univ' / 'loose-file.md').write_text('x')
    env.setenv('STUDY_VAULT_ROOT', str(vault))
    seed_vault()
    assert 'Added 2 courses' in capsys.readouterr().out
    assert sorted(c['title'] for c in rows('courses')) == ['NLP', 'ROB 306']
    seed_vault()                                            # running it again adds nothing
    assert 'Added 0 courses' in capsys.readouterr().out
    assert len(rows('courses')) == 2


@pytest.fixture
def models_server():
    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen['auth'] = self.headers.get('Authorization')
            body = json.dumps({'data': [{'id': 'models/gemini-x'}, {'id': 'llama-chat'}, {'id': 'whisper-large'}]}).encode()
            self.send_response(200 if self.path == '/v1/models' else 404)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = _serve(Handler)
    yield f'http://127.0.0.1:{server.server_port}/v1', seen
    server.shutdown()


def test_available_models_lists_ids_and_survives_errors(models_server, capsys):
    from studyhub.__main__ import available_models
    base, seen = models_server
    assert available_models(base + '/', 'key-1') == ['gemini-x', 'llama-chat', 'whisper-large']
    assert seen['auth'] == 'Bearer key-1'
    assert available_models('http://127.0.0.1:9/v1', 'key') == []   # nothing listening
    assert 'could not list models' in capsys.readouterr().out


def test_pick_model_keeps_valid_and_asks_for_invalid(models_server, monkeypatch, capsys):
    from studyhub import __main__ as cli
    base, _ = models_server
    assert cli.pick_model(base, 'k', 'llama-chat') == 'llama-chat'
    assert 'OK: llama-chat is available' in capsys.readouterr().out
    monkeypatch.setattr('builtins.input', lambda prompt='': '')
    assert cli.pick_model(base, 'k', 'missing-model') == 'gemini-x'  # empty answer -> first chat model
    out = capsys.readouterr().out
    assert 'not available' in out and 'whisper' not in out          # speech models are not offered
    monkeypatch.setattr('builtins.input', lambda prompt='': 'llama-chat')
    assert cli.pick_model(base, 'k', 'missing-model') == 'llama-chat'


def test_main_init_writes_private_config(env, tmp_path, monkeypatch, capsys):
    from studyhub import __main__ as cli
    config = tmp_path / 'conf' / 'config.json'
    env.setenv('STUDY_CONFIG_FILE', str(config))
    answers = iter(['a-long-enough-pass', 'a-long-enough-pass'])
    monkeypatch.setattr(cli.getpass, 'getpass', lambda prompt='': next(answers))
    monkeypatch.setattr('sys.argv', ['studyhub', 'init', '--vault-root', str(tmp_path)])
    cli.main()
    saved = json.loads(config.read_text())
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert 'a-long-enough-pass' not in config.read_text()
    assert saved['STUDY_VAULT_ROOT'] == str(tmp_path.resolve()) and ':' in saved['STUDY_PASSWORD_HASH']
    assert 'Password was not stored in plain text' in capsys.readouterr().out


@pytest.mark.parametrize('answers', [['short', 'short'], ['a-long-enough-pass', 'another-long-pass']], ids=['too-short', 'mismatch'])
def test_main_init_rejects_bad_passwords(env, tmp_path, monkeypatch, answers):
    from studyhub import __main__ as cli
    env.setenv('STUDY_CONFIG_FILE', str(tmp_path / 'config.json'))
    it = iter(answers)
    monkeypatch.setattr(cli.getpass, 'getpass', lambda prompt='': next(it))
    monkeypatch.setattr('sys.argv', ['studyhub', 'password'])
    with pytest.raises(SystemExit):
        cli.main()
    assert not (tmp_path / 'config.json').exists()


# ---------------------------------------------------------------- accounts

def test_all_users_and_check_quota(users, env):
    from fastapi import HTTPException
    from studyhub import accounts
    assert accounts.all_users() == []                       # no accounts database yet
    users('alice'); users('bob')
    everyone = accounts.all_users()
    assert sorted(u['username'] for u in everyone) == ['alice', 'bob']
    alice = next(u for u in everyone if u['username'] == 'alice')
    accounts.check_quota()                                  # nobody signed in on this thread: no limit
    token = accounts.CURRENT_USER.set(alice)
    try:
        accounts.check_quota(extra=1024)                    # well under the default 1 GB
        env.setenv('STUDY_USER_QUOTA_MB', '1')
        with pytest.raises(HTTPException) as error:
            accounts.check_quota(extra=2 * 1024 * 1024)
        assert error.value.status_code == 413
    finally:
        accounts.CURRENT_USER.reset(token)


def test_all_users_hides_disabled_accounts(users):
    from studyhub import accounts
    users('carol'); users('dave')
    with accounts.accounts_db() as db:
        db.execute("UPDATE users SET disabled=1 WHERE username='dave'")
    assert [u['username'] for u in accounts.all_users()] == ['carol']


# ---------------------------------------------------------------- app: lifespan + markdown links

def test_lifespan_starts_and_stops_the_reminder_worker(env, monkeypatch):
    from studyhub import reminders
    from studyhub.app import app
    state = {}

    async def fake_loop():
        state['started'] = True
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            state['cancelled'] = True
            raise

    monkeypatch.setattr(reminders, 'reminder_loop', fake_loop)
    env.setenv('STUDY_REMINDERS_ENABLED', 'true')
    with TestClient(app) as client:
        client.get('/manifest.webmanifest')
    assert state == {'started': True, 'cancelled': True}
    state.clear()
    env.setenv('STUDY_REMINDERS_ENABLED', 'false')
    with TestClient(app):
        pass
    assert state == {}                                      # disabled: never started


@pytest.fixture
def vault(env, tmp_path):
    root = tmp_path / 'vault'
    course = root / '1 Univ' / 'NLP'
    course.mkdir(parents=True)
    (course / 'Lecture 1.md').write_text('# Tokens\n\nhello')
    (course / 'diagram.png').write_bytes(b'\x89PNG\r\n')
    (root / '1 Univ' / 'Other').mkdir()
    (root / '1 Univ' / 'Other' / 'Unique Note.md').write_text('x')
    env.setenv('STUDY_VAULT_ROOT', str(root))
    return root


def test_markdown_wiki_links_resolve_inside_the_vault(vault):
    from studyhub.app import markdown
    html = markdown('[[Lecture 1]] [[Lecture 1#Tokens|see tokens]] [[Unique Note]] [[Nope]] ![[diagram.png]]',
                    relative='1 Univ/NLP/Index.md')
    assert 'href="/api/vault/note?path=1%20Univ/NLP/Lecture%201.md"' in html
    assert '#tokens' in html and '>see tokens<' in html
    assert 'path=1%20Univ/Other/Unique%20Note.md' in html  # found anywhere when the name is unique
    assert 'Nope (غير موجود في الفولدرات المربوطة)' in html
    assert '<img src="/api/vault/file?path=1%20Univ/NLP/diagram.png"' in html


def test_markdown_image_and_link_rules(vault):
    from studyhub.app import markdown
    html = markdown('![alt](diagram.png) ![gone](missing.png) [lec](Lecture%201.md) [web](https://example.com) '
                    '[anchor](#top) [up](../../../etc/passwd)', relative='1 Univ/NLP/Index.md')
    assert 'src="/api/vault/file?path=1%20Univ/NLP/diagram.png"' in html and 'loading="lazy"' in html
    assert 'src="/missing-attachment"' in html
    assert 'href="/api/vault/note?path=1%20Univ/NLP/Lecture%201.md"' in html
    assert 'href="https://example.com" target="_blank" rel="noopener"' in html
    assert 'href="#top"' in html
    assert '/etc/passwd' not in html.replace('href="../../../etc/passwd"', '') and 'api/vault/file?path=../' not in html


# ---------------------------------------------------------------- assistant

def test_assistant_settings_reads_env(env):
    from studyhub import assistant
    env.delenv('STUDY_LLM_BASE_URL', raising=False)
    assert assistant.settings() == (assistant.DEFAULT_BASE.rstrip('/'), '', assistant.DEFAULT_MODEL)
    env.setenv('STUDY_LLM_BASE_URL', 'https://llm.example/v1/'); env.setenv('STUDY_LLM_API_KEY', 'k'); env.setenv('STUDY_LLM_MODEL', 'm')
    assert assistant.settings() == ('https://llm.example/v1', 'k', 'm')


@pytest.mark.parametrize('code,body,expected_code,expected_text', [
    (429, '{"error":{"message":"slow down"}}', 429, 'وصلت للحد المجاني'),
    (503, 'busy', 503, 'عليه ضغط'),
    (400, '[{"error":{"message":"bad model id"}}]', 502, 'bad model id'),
    (401, 'not json at all', 502, 'not json at all'),
])
def test_model_error_messages(code, body, expected_code, expected_text):
    from studyhub.assistant import model_error
    error = urllib.error.HTTPError('https://x', code, 'err', {}, io.BytesIO(body.encode()))
    status, message = model_error(error, 'my-model')
    assert status == expected_code and expected_text in message and message.startswith('my-model')


def test_get_own_key_never_returns_the_key(personal):
    reply = personal.get('/api/assistant/key')
    assert reply.status_code == 200
    data = reply.json()
    assert data['set'] is False and data['last4'] == ''
    assert set(data['providers']) == {'groq', 'gemini', 'openrouter'}
    assert all(set(p) == {'name', 'keys_url', 'prefix'} for p in data['providers'].values())


# ---------------------------------------------------------------- board, guard, voices

def test_board_status_route(personal):
    reply = personal.get('/api/board')
    assert reply.status_code == 200
    data = reply.json()
    assert {'available', 'enabled', 'last', 'imported', 'error', 'url', 'profile'} <= set(data)
    assert data['enabled'] is True and data['imported'] == 0


def test_guard_reply_sends_json_error():
    from studyhub.guard import _reply
    sent = []

    async def send(message):
        sent.append(message)

    asyncio.run(_reply(send, 429, 'استنى شوية', extra=[(b'retry-after', b'30')]))
    start, body = sent
    headers = dict(start['headers'])
    assert start['status'] == 429 and headers[b'retry-after'] == b'30' and headers[b'x-content-type-options'] == b'nosniff'
    assert json.loads(body['body']) == {'detail': 'استنى شوية'}
    assert headers[b'content-length'] == str(len(body['body'])).encode()


def test_pick_voice(personal):
    from studyhub.voices import folder, pick_voice
    assert pick_voice('day') == ''
    (folder('day') / '1700000000__my clip.mp3').write_bytes(b'ID3')
    assert pick_voice('day') == '/api/voices/day/1700000000__my%20clip.mp3'


# ---------------------------------------------------------------- MCP

def test_mcp_token_valid(env):
    from studyhub.mcp_server import token_valid
    token = 'x' * 40
    assert token_valid(token) is False                     # no token configured
    env.setenv('STUDY_API_TOKEN_HASH', hashlib.sha256(token.encode()).hexdigest())
    assert token_valid(token) is True
    assert token_valid('y' * 40) is False
    assert token_valid('') is False and token_valid('x' * 10) is False   # too short


def test_mcp_single_owner_token_over_http(env):
    from studyhub.app import app
    token = 'tok' * 15
    env.setenv('STUDY_API_TOKEN_HASH', hashlib.sha256(token.encode()).hexdigest())
    client = TestClient(app)
    call = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}
    assert client.post('/mcp', json=call).status_code == 401
    assert client.post('/mcp', json=call, headers={'Authorization': 'Bearer ' + token}).status_code == 200
    assert client.post('/mcp/' + token, json=call).status_code == 200
    assert client.post('/mcp/' + 'nope' * 10, json=call).status_code == 401


def test_mcp_read_note_tool(personal, vault):
    from studyhub.mcp_server import tool_read_note
    note = personal.post('/api/notes', json={'title': 'Mine', 'content': 'body text'}).json()
    assert tool_read_note({'note_id': note['id']}) == {'title': 'Mine', 'truncated': False, 'markdown': 'body text'}
    by_path = tool_read_note({'path': '1 Univ/NLP/Lecture 1.md'})
    assert by_path['title'] == 'Lecture 1' and by_path['markdown'].startswith('# Tokens')
    with pytest.raises(ValueError):
        tool_read_note({'path': '1 Univ/NLP/diagram.png'})
    with pytest.raises(ValueError):
        tool_read_note({})


def test_mcp_rpc_error_shapes():
    from studyhub.mcp_server import handle, rpc_error
    assert rpc_error(7, -32601, 'nope') == {'jsonrpc': '2.0', 'id': 7, 'error': {'code': -32601, 'message': 'nope'}}
    assert handle({'id': 3})['error']['code'] == -32600
    assert handle('not a dict')['id'] is None


def test_mcp_logs_never_show_the_url_token():
    from studyhub.mcp_server import RedactTokens
    record = logging.LogRecord('uvicorn.access', logging.INFO, __file__, 1, '%s "%s"', ('1.2.3.4', 'POST /mcp/secret123?x=1 HTTP/1.1'), None)
    assert RedactTokens().filter(record) is True
    assert 'secret123' not in record.getMessage() and '/mcp/***' in record.getMessage()


# ---------------------------------------------------------------- reminders (web push is faked)

def test_send_push_uses_vapid_and_blocks_redirects(personal, monkeypatch):
    import pywebpush
    from studyhub import reminders
    calls = {}

    class Redirect(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302); self.send_header('Location', 'http://127.0.0.1:9/elsewhere'); self.end_headers()

        def log_message(self, *args):
            pass

    trap = _serve(Redirect)

    def fake_webpush(**kwargs):
        calls.update(kwargs)
        calls['redirects'] = kwargs['requests_session'].request.__qualname__
        reply = kwargs['requests_session'].request('GET', f'http://127.0.0.1:{trap.server_port}/push', allow_redirects=True)
        calls['status'] = reply.status_code                 # the redirect is returned, never followed

    monkeypatch.setattr(pywebpush, 'webpush', fake_webpush)
    subscription = {'endpoint': 'https://fcm.googleapis.com/fcm/send/abc', 'keys': push_keys()}
    reminders.send_push(subscription, {'title': 'تجربة'})
    assert calls['subscription_info']['endpoint'] == subscription['endpoint']
    assert json.loads(calls['data']) == {'title': 'تجربة'} and calls['ttl'] == 3600
    assert calls['vapid_claims']['sub'].startswith('mailto:') and os.path.isfile(calls['vapid_private_key'])
    assert 'NoRedirectSession' in calls['redirects']
    assert calls['status'] == 302
    trap.shutdown()
    with pytest.raises(ValueError):
        reminders.send_push({'endpoint': 'https://evil.example/x', 'keys': push_keys()}, {})


def test_test_notification_route(personal, monkeypatch):
    from studyhub import reminders
    sent = []
    monkeypatch.setattr(reminders, 'send_push', lambda sub, payload: sent.append(payload))
    endpoint = {'endpoint': 'https://fcm.googleapis.com/fcm/send/route'}
    assert personal.post('/api/notifications/push/test', json=endpoint).status_code == 404   # not subscribed
    subscribe(personal, endpoint['endpoint'])
    assert personal.post('/api/notifications/push/test', json=endpoint).json() == {'accepted': True}
    assert sent[0]['tag'] == 'studyhub-test'
    assert personal.post('/api/notifications/push/test', json=endpoint).status_code == 429   # 30 s cooldown


def test_test_notification_reports_push_failure(personal, monkeypatch):
    from studyhub import reminders

    def broken(sub, payload):
        raise RuntimeError('push service down')

    monkeypatch.setattr(reminders, 'send_push', broken)
    endpoint = 'https://fcm.googleapis.com/fcm/send/broken'
    subscribe(personal, endpoint)
    assert personal.post('/api/notifications/push/test', json={'endpoint': endpoint}).status_code == 502


def test_task_events():
    from studyhub.reminders import task_events
    now = time.time()
    past = datetime.fromtimestamp(now - 60, CAIRO).isoformat()
    old = datetime.fromtimestamp(now - 3 * 86400, CAIRO).isoformat()
    assert task_events({'status': 'done', 'remind_at': past}, now) == []
    assert task_events({'remind_at': old}, now) == []                       # older than a day: skipped
    assert task_events({'remind_at': past}, now)[0][:2] == ('reminder', past)
    finished = {'state': 'finished', 'run_id': 'r1', 'finished_at': now - 5}
    assert ('timer', 'r1', now - 5) in task_events({'timer': finished}, now)
    assert task_events({'timer': finished | {'finished_at': now - 2 * 86400}}, now) == []


def make_task(client, **fields):
    task = client.post('/api/tasks', json={'title': 'تقرير ROB 310'}).json()
    from studyhub.app import get, put
    put('tasks', get('tasks', task['id']) | fields)
    return task['id']


def deliveries():
    from studyhub.app import database
    with database() as db:
        return [dict(r) for r in db.execute('SELECT * FROM reminder_deliveries')]


def test_collect_and_dispatch_a_reminder(personal, monkeypatch):
    from studyhub import reminders
    sent = []
    monkeypatch.setattr(reminders, 'send_push', lambda sub, payload: sent.append(payload))
    subscribe(personal)
    now = time.time()
    task_id = make_task(personal, remind_at=datetime.fromtimestamp(now - 30, CAIRO).isoformat())
    reminders.collect_jobs(now)
    reminders.collect_jobs(now)                                             # idempotent
    jobs = deliveries()
    assert len(jobs) == 1 and jobs[0]['status'] == 'pending'
    reminders.dispatch_job(jobs[0]['id'], now)
    assert sent == [{'title': 'مساحتي — تذكير مهمة', 'body': 'تقرير ROB 310', 'tag': 'studyhub-' + jobs[0]['id'], 'url': '/?task=' + task_id}]
    assert deliveries()[0]['status'] == 'sent'
    reminders.dispatch_job(jobs[0]['id'], now)                              # already sent: nothing happens
    assert len(sent) == 1


def test_collect_finishes_expired_timers(personal):
    from studyhub import reminders
    from studyhub.app import get
    now = time.time()
    task_id = make_task(personal, timer={'state': 'running', 'run_id': 'run9', 'ends_at': now - 10, 'remaining_seconds': 30})
    reminders.collect_jobs(now)
    timer = get('tasks', task_id)['timer']
    assert timer['state'] == 'finished' and timer['remaining_seconds'] == 0 and timer['finished_at'] == now - 10


def test_dispatch_cancels_when_task_is_done(personal, monkeypatch):
    from studyhub import reminders
    from studyhub.app import get, put
    monkeypatch.setattr(reminders, 'send_push', lambda *a: pytest.fail('should not send'))
    subscribe(personal)
    now = time.time()
    task_id = make_task(personal, remind_at=datetime.fromtimestamp(now - 30, CAIRO).isoformat())
    reminders.collect_jobs(now)
    put('tasks', get('tasks', task_id) | {'status': 'done'})
    reminders.dispatch_job(deliveries()[0]['id'], now)
    assert deliveries()[0]['status'] == 'cancelled'


def test_dispatch_deadline_stage_with_voice(personal, monkeypatch):
    from studyhub import reminders
    from studyhub.voices import folder
    sent = []
    monkeypatch.setattr(reminders, 'send_push', lambda sub, payload: sent.append(payload))
    (folder('hour') / '1__alarm.mp3').write_bytes(b'ID3')
    subscribe(personal)
    now = time.time()
    due = datetime.fromtimestamp(now + 50 * 60, CAIRO).isoformat()          # the 1-hour stage has just passed
    make_task(personal, due=due, created=now - 7200)
    reminders.run_once(now)
    assert len(sent) == 1
    assert sent[0]['stage'] == 'hour' and sent[0]['voice'] == '/api/voices/hour/1__alarm.mp3'
    assert sent[0]['title'] == 'مساحتي — فاضل ساعة على الديدلاين'


class Gone(Exception):
    def __init__(self, code):
        self.response = type('R', (), {'status_code': code})()


def test_dispatch_removes_dead_subscriptions_and_retries_others(personal, monkeypatch):
    from studyhub import reminders
    from studyhub.app import database
    subscribe(personal)
    now = time.time()
    make_task(personal, remind_at=datetime.fromtimestamp(now - 30, CAIRO).isoformat())
    reminders.collect_jobs(now)
    job = deliveries()[0]['id']

    def temporary(sub, payload):
        raise Gone(500)

    monkeypatch.setattr(reminders, 'send_push', temporary)
    reminders.dispatch_job(job, now)
    row = deliveries()[0]
    assert row['status'] == 'pending' and row['attempts'] == 1 and row['next_try'] > now   # retried later

    def gone(sub, payload):
        raise Gone(410)

    monkeypatch.setattr(reminders, 'send_push', gone)
    reminders.dispatch_job(job, row['next_try'])
    assert deliveries()[0]['status'] == 'cancelled'
    with database() as db:
        assert db.execute('SELECT COUNT(*) FROM push_subscriptions').fetchone()[0] == 0


def test_run_once_records_the_worker_tick(personal):
    from studyhub import reminders
    from studyhub.app import setting
    reminders.run_once()
    tick = setting('reminder_worker', {})
    assert tick['ok'] is True and time.time() - tick['last_tick'] < 60


class StopLoop(Exception):
    pass


def run_loop_once(monkeypatch):
    from studyhub import reminders

    async def stop(seconds):
        raise StopLoop

    monkeypatch.setattr(reminders.asyncio, 'sleep', stop)
    with pytest.raises(StopLoop):
        asyncio.run(reminders.reminder_loop())


def test_reminder_loop_single_owner(personal, monkeypatch):
    from studyhub import board, reminders
    calls = []
    monkeypatch.setattr(reminders, 'settle_timers', lambda: calls.append('settle'))
    monkeypatch.setattr(reminders, 'run_once', lambda: calls.append('run'))
    monkeypatch.setattr(board, 'sync_quietly', lambda: calls.append('board'))
    run_loop_once(monkeypatch)
    assert calls == ['settle', 'run', 'board']


def test_reminder_loop_runs_for_every_user_and_survives_errors(users, monkeypatch):
    from studyhub import accounts, board, reminders
    users('erin'); users('frank')
    seen = []

    def run():
        user = accounts.CURRENT_USER.get()
        seen.append(user['username'])
        if user['username'] == 'erin':
            raise RuntimeError('one user failing must not stop the others')

    monkeypatch.setattr(reminders, 'settle_timers', lambda: None)
    monkeypatch.setattr(reminders, 'run_once', run)
    monkeypatch.setattr(board, 'sync_quietly', lambda: None)
    run_loop_once(monkeypatch)
    assert sorted(seen) == ['erin', 'frank']
    assert accounts.CURRENT_USER.get() is None


def test_reminder_loop_marks_worker_unhealthy(personal, monkeypatch):
    from studyhub import reminders
    from studyhub.app import setting

    def broken():
        raise RuntimeError('database locked')

    monkeypatch.setattr(reminders, 'settle_timers', broken)
    run_loop_once(monkeypatch)
    assert setting('reminder_worker', {})['ok'] is False
