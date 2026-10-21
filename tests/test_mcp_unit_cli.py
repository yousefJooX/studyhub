"""MCP server tools, pure helpers, the CLI and the deploy scripts."""
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from studyhub.app import app

from .conftest import ROOT

PKG = Path(__file__).resolve().parents[1]


def mcp(client, method, params=None, token=None):
    headers = {'Authorization': f'Bearer {token}'} if token else {}
    response = client.post('/mcp', json={'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def call(client, token, name, **arguments):
    result = mcp(client, 'tools/call', {'name': name, 'arguments': arguments}, token)['result']
    assert not result.get('isError'), result
    return json.loads(result['content'][0]['text'])


def test_mcp_every_tool(users, env, tmp_path):
    alice = users('alice')
    alice.post('/api/vault/upload-file?path=Notes/NLP/attention.md', content=b'# Attention\nQuery key value attention.')
    token = alice.post('/api/account/token').json()['token']
    client = TestClient(app)
    assert mcp(client, 'initialize', {'protocolVersion': '2025-06-18'}, token)['result']['serverInfo']
    tools = {t['name'] for t in mcp(client, 'tools/list', token=token)['result']['tools']}
    assert tools == {'get_today', 'list_tasks', 'create_task', 'update_task', 'list_courses', 'get_schedule', 'search_notes',
                     'read_note', 'create_note', 'list_files', 'weekly_review', 'create_course', 'create_slot'}
    course = call(client, token, 'create_course', title='NLP')
    task = call(client, token, 'create_task', title='Sheet 1', course='nlp', due='2030-01-01T23:59')['created']
    assert task['course'] == 'NLP'
    assert call(client, token, 'update_task', task_id=task['id'], status='done')['updated']['status'] == 'done'
    assert call(client, token, 'list_tasks', status='done')['tasks'][0]['title'] == 'Sheet 1'
    assert call(client, token, 'create_slot', title='NLP lecture', course='NLP', day='Sunday', start='09:00', end='11:00', kind='lecture')
    assert call(client, token, 'get_schedule')
    assert call(client, token, 'list_courses')['courses'][0]['title'] == 'NLP'
    call(client, token, 'create_note', title='My note', course='NLP', content='hello world')
    hits = call(client, token, 'search_notes', query='attention')
    assert 'attention' in json.dumps(hits).lower()
    assert call(client, token, 'list_files')
    assert call(client, token, 'get_today')['now']
    assert 'focus_minutes' in call(client, token, 'weekly_review')
    assert course
    denied = client.post('/mcp', json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
    assert denied.status_code == 401
    assert client.get('/mcp').status_code in (401, 405)


def test_markdown_renderer():
    from studyhub.app import markdown
    html = markdown('# T\n\n> [!warning] Careful\n> body\n\n- [x] done\n- [ ] todo\n\n==mark== ~~old~~ %%hidden%%\n\n'
                    '$$E=mc^2$$\n\n```mermaid\nflowchart LR\nA-->B\n```\n\n<img src=x onerror=alert(1)>\n[x](javascript:alert(1))')
    assert 'callout-warning' in html and 'task-box done' in html and '<mark>' in html and '<s>' in html
    assert '%%hidden%%' not in html and ' hidden' not in html.replace('aria-hidden', '') and 'mermaid' in html
    assert '<img' not in html and 'href="javascript' not in html          # escaped text, never a live tag or link


def test_board_parsers():
    from studyhub.board import parse_board, parse_courses, parse_deadline
    page = (PKG / 'tests' / 'fixtures' / 'board' / 'main.html').read_text()
    posts = parse_board(page)
    kinds = [p['kind'] for p in posts]
    assert kinds.count('assignment') == 5 and kinds.count('announcement') == 4
    first = [p for p in posts if p['id'] == '1'][0]
    assert first['course_code'] == 'ROB 401' and first['course_name'] == 'Robot Design' and first['posted']
    courses = parse_courses(page)
    assert {'code': 'ROB 402', 'name': 'Robot Arms'} in courses and len(courses) == 7
    assert parse_deadline('BEGIN:VEVENT\r\nDTSTART:20301024T002714Z\r\n').startswith('2030-10-24T00:27:14')
    assert parse_deadline('nothing') == ''


def test_keystore_roundtrip(env):
    from studyhub.keystore import seal, unseal
    sealed = seal('gsk_secret')
    assert sealed != 'gsk_secret' and unseal(sealed) == 'gsk_secret' and unseal('garbage') == ''


def test_client_ip_trusts_only_last_proxy_hop():
    from studyhub.guard import client_key
    scope = {'client': ('127.0.0.1', 1), 'headers': [(b'x-forwarded-for', b'6.6.6.6, 1.2.3.4')]}
    assert client_key(scope) == '1.2.3.4'
    assert client_key({'client': ('8.8.8.8', 1), 'headers': [(b'x-forwarded-for', b'6.6.6.6')]}) == '8.8.8.8'


def test_week_starts_saturday():
    from datetime import date
    from studyhub.insights import week_start
    assert week_start(date(2026, 10, 22)) == date(2026, 10, 17)      # Thursday → Saturday before
    assert week_start(date(2026, 10, 17)) == date(2026, 10, 17)


def run_cli(env_vars, *args, stdin=''):
    environment = {**os.environ, **env_vars, 'PYTHONPATH': str(ROOT)}
    return subprocess.run([sys.executable, '-m', 'studyhub', *args], input=stdin, text=True, capture_output=True,
                          cwd=ROOT, env=environment, timeout=120)


def test_cli_users_and_token(tmp_path):
    data = tmp_path / 'friends'
    env_vars = {'STUDY_CONFIG_FILE': str(data / 'config.json'), 'STUDY_DATA_DIR': str(data), 'STUDY_MULTIUSER': '1'}
    data.mkdir()
    (data / 'config.json').write_text(json.dumps({'STUDY_DATA_DIR': str(data), 'STUDY_MULTIUSER': '1'}))
    from studyhub import accounts
    old = os.environ.get('STUDY_DATA_DIR')
    os.environ['STUDY_DATA_DIR'] = str(data)
    try:
        with accounts.accounts_db() as db:
            db.execute("INSERT INTO users(id,username,name,password,created) VALUES ('a'*1,'zed','Zed',?,0)", (accounts.hash_password('goodpass99'),))
    finally:
        os.environ['STUDY_DATA_DIR'] = old or ''
    listed = run_cli(env_vars, 'users', 'list')
    assert listed.returncode == 0 and 'zed' in listed.stdout
    assert run_cli(env_vars, 'users', 'college', 'zed').returncode == 0
    assert 'FCI' in run_cli(env_vars, 'users', 'list').stdout
    assert run_cli(env_vars, 'users', 'disable', 'zed').returncode == 0 and 'DISABLED' in run_cli(env_vars, 'users', 'list').stdout
    assert run_cli(env_vars, 'users', 'disable', 'ghost').returncode != 0
    personal = tmp_path / 'personal'
    personal.mkdir()
    (personal / 'config.json').write_text('{}')
    token = run_cli({'STUDY_CONFIG_FILE': str(personal / 'config.json'), 'STUDY_PUBLIC_ORIGIN': 'https://x.test',
                     'STUDY_TOKEN_URL_ONLY': '1'}, 'token')
    assert token.returncode == 0 and token.stdout.strip().startswith('https://x.test/mcp/')
    assert 'STUDY_API_TOKEN_HASH' in json.loads((personal / 'config.json').read_text())


def test_deploy_scripts_are_valid_bash():
    for script in (PKG / 'deploy').glob('*.sh'):
        assert subprocess.run(['bash', '-n', str(script)]).returncode == 0, script
    example = (PKG / 'deploy' / 'deploy.env.example').read_text()
    assert 'SERVER=' in example and 'COLLEGE_CODE=' in example


def test_no_secrets_or_personal_data_in_repo():
    import re
    pattern = re.compile(r'gsk_[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{30}|192\.168\.|/home/joox|BEGIN (RSA|OPENSSH) PRIVATE')
    for path in PKG.rglob('*'):
        if path.is_file() and path != Path(__file__).resolve() and '.git' not in path.parts and 'vendor' not in path.parts and path.suffix in {'.py', '.js', '.sh', '.md', '.html', '.json', '.example', '.css'}:
            assert not pattern.search(path.read_text(errors='ignore')), path
