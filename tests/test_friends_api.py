"""The friends (multi-user) site: accounts, isolation, college access, quotas, assistant keys, board, archive."""
import glob
import io
import json
import zipfile

from fastapi.testclient import TestClient

from studyhub.app import app


def test_signup_rules(users):
    users('alice')
    client = TestClient(app)
    ip = {'x-forwarded-for': '10.9.9.9'}
    assert client.post('/api/signup', json={'name': 'A', 'username': 'alice', 'password': 'goodpass99'}, headers=ip).status_code == 409
    assert client.post('/api/signup', json={'name': 'A', 'username': 'AB', 'password': 'goodpass99'}, headers=ip).status_code == 400
    assert client.post('/api/signup', json={'name': 'A', 'username': 'weakpw', 'password': 'password'}, headers=ip).status_code == 400
    assert client.post('/api/signup', json={'name': 'A', 'username': 'short', 'password': 'short'}, headers=ip).status_code == 400


def test_signup_without_faculty_code_works(users):
    """Regression: the hidden level field used to make sign-up without a code fail."""
    client = TestClient(app)
    body = {'name': 'Outside', 'username': 'outside', 'password': 'goodpass99', 'college_code': '', 'level': '', 'program': ''}
    response = client.post('/api/signup', json=body, headers={'x-forwarded-for': '10.8.0.1'})
    assert response.status_code == 200, response.text
    assert response.json()['user']['college'] is False


def test_faculty_code_level_program(users):
    client = TestClient(app)
    ip = {'x-forwarded-for': '10.8.0.2'}
    body = {'name': 'S', 'username': 'stud', 'password': 'goodpass99'}
    assert client.post('/api/signup', json=body | {'college_code': 'wrong', 'level': 3}, headers=ip).status_code == 403
    assert client.post('/api/signup', json=body | {'college_code': 'cs'}, headers=ip).status_code == 400  # level required
    ok = client.post('/api/signup', json=body | {'college_code': 'cs', 'level': '3', 'program': 'rse'}, headers=ip).json()
    assert ok['user'] | {} == ok['user'] and ok['user']['college'] and ok['user']['level'] == 3 and ok['user']['program'] == 'rse'
    assert client.get('/api/state').json()['college'] == {'college': True, 'level': 3, 'program': 'rse'}
    assert client.put('/api/profile/college', json={'level': 4, 'program': 'ds'}).status_code == 200
    assert client.put('/api/profile/college', json={'level': 4, 'program': 'xyz'}).status_code == 400


def test_invite_code(users, env):
    env.setenv('STUDY_SIGNUP_CODE', 'friends-only')
    client = TestClient(app)
    ip = {'x-forwarded-for': '10.8.0.3'}
    body = {'name': 'I', 'username': 'invitee', 'password': 'goodpass99'}
    assert client.post('/api/signup', json=body, headers=ip).status_code == 403
    assert client.post('/api/signup', json=body | {'code': 'friends-only'}, headers=ip).status_code == 200


def test_signup_closed_and_max_users(users, env):
    env.setenv('STUDY_SIGNUP', 'closed')
    assert TestClient(app).post('/api/signup', json={'name': 'x', 'username': 'xxx', 'password': 'goodpass99'}).status_code == 403
    env.setenv('STUDY_SIGNUP', 'open')
    env.setenv('STUDY_MAX_USERS', '1')
    users('first')
    assert TestClient(app).post('/api/signup', json={'name': 'x', 'username': 'second', 'password': 'goodpass99'},
                                headers={'x-forwarded-for': '10.8.0.4'}).status_code == 403


def test_login_logout_password_change(users):
    alice = users('alice')
    client = TestClient(app)
    assert client.post('/api/login', json={'username': 'alice', 'password': 'nope'}).status_code == 401
    assert client.post('/api/login', json={'username': 'ALICE', 'password': 'goodpass99'}).status_code == 200
    assert alice.post('/api/account/password', json={'old': 'wrong', 'new': 'newpass123'}).status_code == 400
    assert alice.post('/api/account/password', json={'old': 'goodpass99', 'new': 'newpass123'}).json()['relogin']
    assert client.get('/api/state').status_code == 401                    # all sessions closed
    assert client.post('/api/login', json={'username': 'alice', 'password': 'newpass123'}).status_code == 200
    assert client.post('/api/logout').status_code == 200 and client.get('/api/state').status_code == 401


def test_users_are_isolated(users):
    alice, bob = users('alice'), users('bob')
    task = alice.post('/api/tasks', json={'title': 'secret'}).json()
    note = alice.post('/api/notes', json={'title': 'n', 'content': 'secret'}).json()
    assert bob.get('/api/state').json()['tasks'] == []
    assert bob.put(f"/api/tasks/{task['id']}", json={'title': 'x'}).status_code == 404
    assert bob.delete(f"/api/tasks/{task['id']}").status_code == 404
    assert bob.get(f"/api/notes/{note['id']}").status_code == 404
    assert len(alice.get('/api/state').json()['tasks']) == 1


def test_mcp_token_per_user(users):
    alice, bob = users('alice'), users('bob')
    alice.post('/api/tasks', json={'title': 'alice task'})
    token = alice.post('/api/account/token').json()['token']
    call = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'list_tasks', 'arguments': {}}}
    raw = TestClient(app).post(f'/mcp/{token}', json=call).json()
    assert 'alice task' in json.dumps(raw)
    assert TestClient(app).post('/mcp', json=call, headers={'Authorization': f'Bearer {token}'}).status_code == 200
    assert alice.get('/api/account').json()['token_created']
    assert alice.delete('/api/account/token').status_code == 200                    # regression: was shadowed (404)
    assert TestClient(app).post(f'/mcp/{token}', json=call).status_code == 401
    assert bob.get('/api/account').json()['token_created'] is None


def test_vault_upload_folder_zip_and_delete(users):
    alice = users('alice')
    assert alice.post('/api/vault/upload-file?path=Notes/NLP/a.md', content=b'# A').status_code == 200
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as z:
        z.writestr('MyVault/CV/l1.md', '# L1')
        z.writestr('MyVault/.obsidian/x.json', '{}')
        z.writestr('../escape.md', 'x')
    result = alice.post('/api/vault/upload-zip?name=MyVault.zip', content=buffer.getvalue()).json()
    assert result['files'] == 1 and result['skipped'] == 2
    names = {f['name'] for f in alice.get('/api/vault').json()['files']}
    assert {'a.md', 'l1.md'} <= names
    assert alice.delete('/api/vault/folder?name=Notes').status_code == 200          # regression: was shadowed (404)
    assert 'a.md' not in {f['name'] for f in alice.get('/api/vault').json()['files']}


def test_storage_quota(users, env):
    env.setenv('STUDY_USER_QUOTA_MB', '1')
    alice = users('alice')
    assert alice.post('/api/uploads?name=big.pdf', content=b'0' * (2 * 1024 * 1024)).status_code == 413
    assert alice.get('/api/account').json()['quota_mb'] == 1


def test_archive_only_for_faculty_students(users, archive):
    outsider = users('outsider')
    student = users('student', college_code='cs', level=4, program='rse')
    paths = ['/api/archive', '/api/archive/y4/c1/items', '/api/archive/file/y4/i1/0']
    assert [outsider.get(p).status_code for p in paths] == [403, 403, 403]
    assert outsider.get('/api/state').json()['archive'] is False
    assert [student.get(p).status_code for p in paths] == [200, 200, 200]
    assert student.get('/api/archive/file/y4/i1/0').content == b'%PDF-1.4 lecture'
    assert student.post('/api/archive/import', json={'account': 'y4', 'course': 'c1'}).status_code == 200
    assert outsider.post('/api/account/college', json={'code': 'nope', 'level': 2}).status_code == 403
    assert outsider.post('/api/account/college', json={'code': 'cs', 'level': 2, 'program': 'ds'}).status_code == 200
    assert outsider.get('/api/archive').status_code == 200


def test_own_ai_key(users, fake_llm, tmp_path):
    alice, bob = users('alice'), users('bob')
    assert alice.get('/api/assistant/status').json()['configured'] is False
    assert alice.post('/api/assistant', json={'messages': [{'role': 'user', 'content': 'hi'}]}).status_code == 503
    assert alice.post('/api/assistant/key', json={'provider': 'groq', 'key': 'gsk_bad_0000000000'}).status_code == 400
    saved = alice.post('/api/assistant/key', json={'provider': 'groq', 'key': 'gsk_good_1234567890'}).json()
    assert saved['model'] == 'openai/gpt-oss-120b' and saved['last4'] == '7890'
    raw = b''.join(open(f, 'rb').read() for f in glob.glob(str(tmp_path / 'data' / 'users' / '*' / 'study.sqlite3')))
    assert b'gsk_good_1234567890' not in raw                                       # stored encrypted
    reply = alice.post('/api/assistant', json={'messages': [{'role': 'user', 'content': 'add my lab'}]}).json()
    assert reply['changed'] and {a['tool'] for a in reply['actions']} == {'create_course', 'create_task', 'create_slot'}
    state = alice.get('/api/state').json()
    assert state['tasks'][0]['title'] == 'Lab report' and state['slots'][0]['kind'] == 'lab'
    assert fake_llm.seen[-1] == 'Bearer gsk_good_1234567890'
    assert bob.get('/api/assistant/status').json()['own'] is False                   # keys are per user
    assert alice.delete('/api/assistant/key').status_code == 200 and alice.get('/api/assistant/status').json()['own'] is False


def test_shared_key_daily_cap(users, fake_llm, env):
    env.setenv('STUDY_LLM_BASE_URL', fake_llm.base)
    env.setenv('STUDY_LLM_API_KEY', 'gsk_good_shared')
    env.setenv('STUDY_LLM_MODEL', 'openai/gpt-oss-120b')
    env.setenv('STUDY_ASSISTANT_DAILY', '1')
    alice = users('alice')
    message = {'messages': [{'role': 'user', 'content': 'hi'}]}
    assert alice.post('/api/assistant', json=message).status_code == 200
    assert alice.post('/api/assistant', json=message).status_code == 429
    alice.post('/api/assistant/key', json={'provider': 'groq', 'key': 'gsk_good_own_key_99'})
    assert alice.post('/api/assistant', json=message).status_code == 200           # own key: no cap


def test_board_sync_courses_open_and_closed(users, fake_board):
    student = users('student', college_code='cs', level=4, program='rse')
    mine = student.post('/api/courses', json={'title': 'ROB 401 Robot Design', 'kind': 'college'}).json()
    result = student.post('/api/board/sync').json()
    assert result == result | {'added': 3, 'closed': 2} and 'courses' not in result
    state = student.get('/api/state').json()
    assert [c['title'] for c in state['courses']] == ['ROB 401 Robot Design']        # the board never adds courses
    linked = [t for t in state['tasks'] if t['course_id'] == mine['id']]
    assert linked and all('ROB 401' in t['board']['course'] for t in linked)          # matched by course code
    loose = [t for t in state['tasks'] if not t['course_id']]
    assert loose and all(t['board']['course'] for t in loose)                         # others keep the board's course name
    closed = [t for t in state['tasks'] if t['board']['closed']]
    assert len(closed) == 2 and all(t['status'] == 'done' for t in closed)
    assert all(t['status'] == 'todo' and t['priority'] == 'high' for t in state['tasks'] if not t['board']['closed'])
    assert student.get('/api/weekly').json()['done_count'] == 0                    # closed posts aren't your work
    again = student.post('/api/board/sync').json()
    assert again['added'] == 0 and again['closed'] == 0
    news = student.get('/api/board/announcements').json()
    assert news['available'] and len(news['items']) == 4 and all(i['url'].endswith('/') for i in news['items'])
    assert student.put('/api/board', json={'enabled': False}).json() == {'enabled': False}
    assert student.post('/api/board/sync').json()['skipped'] == 'off'


def test_board_cleans_up_courses_it_wrongly_created(users, fake_board):
    """Older versions made a course for every entry in the board's list (other term, other tracks)."""
    student = users('student', college_code='cs', level=4, program='rse')
    from studyhub import app as core, accounts
    def make(title, code):
        return student.post('/api/courses', json={'title': title, 'kind': 'college'}).json()['id'], code
    wrong, used = make('Computer Vision', 'CV 401'), make('Robot Arms', 'ROB 402')
    token = accounts.CURRENT_USER.set(dict(accounts.user_by('username', 'student')))
    try:
        for cid, code in (wrong, used):
            core.put('courses', core.get('courses', cid) | {'board_code': code})
    finally:
        accounts.CURRENT_USER.reset(token)
    student.post('/api/slots', json={'title': 'Arms lab', 'course_id': used[0], 'day': 0, 'start': '09:00', 'end': '10:00', 'kind': 'lab'})
    student.post('/api/board/sync')
    titles = [c['title'] for c in student.get('/api/state').json()['courses']]
    assert titles == ['Robot Arms']                                                   # unused auto course gone, used one kept


def test_telegram_bot_only_for_students(users, env):
    student = users('student', college_code='cs', level=4, program='rse')
    outsider = users('outsider')
    assert student.get('/api/state').json()['telegram_bot'] == 'HNU_CS_bot'
    assert outsider.get('/api/state').json()['telegram_bot'] == ''
    env.setenv('STUDY_TELEGRAM_BOT', 'bad name"><script>')
    assert student.get('/api/state').json()['telegram_bot'] == ''


def test_board_not_for_outsiders(users, fake_board):
    outsider = users('outsider')
    assert outsider.post('/api/board/sync').status_code == 400
    assert outsider.get('/api/board/announcements').json()['available'] is False


def test_board_down_is_handled(users, env):
    env.setenv('STUDY_BOARD_URL', 'http://127.0.0.1:9')                                  # nothing listens there
    student = users('student', college_code='cs', level=3, program='rse')
    assert student.post('/api/board/sync').status_code == 502
    assert student.get('/api/board/announcements').json()['error'] is True
    from studyhub.board import sync_quietly
    sync_quietly()                                                                     # background worker never raises


# ---------- faculty code must never be bypassable (regression: any code was accepted when none was configured)

def _signup(body, ip):
    return TestClient(app).post('/api/signup', json={'name': 'X', 'password': 'goodpass99', **body}, headers={'x-forwarded-for': ip})


def test_no_faculty_code_configured_means_nobody_gets_college(users, env, archive):
    env.delenv('STUDY_COLLEGE_CODE', raising=False)
    assert TestClient(app).get('/api/auth').json()['college_code'] is False
    for i, body in enumerate([{'college_code': 'anything', 'level': 3}, {'college_code': 'cs', 'level': 3},
                              {'college': True, 'level': 3}, {'college': 'true', 'college_code': '', 'level': 3}]):
        response = _signup({'username': f'nocode{i}', **body}, f'10.7.0.{i}')
        assert response.status_code == 403, (body, response.text)
    plain = _signup({'username': 'plainuser'}, '10.7.1.1')
    assert plain.status_code == 200 and plain.json()['user']['college'] is False
    client = TestClient(app)
    client.post('/api/login', json={'username': 'plainuser', 'password': 'goodpass99'})
    assert client.post('/api/account/college', json={'code': 'anything', 'level': 2}).status_code == 403
    assert client.post('/api/account/college', json={'code': '', 'level': 2}).status_code == 403
    assert client.get('/api/archive').status_code == 403
    assert client.get('/api/state').json()['archive'] is False


def test_faculty_code_must_match_exactly(users):
    for i, code in enumerate(['wrong', 'c', 'css', 'cs ', ' x', '', 'CS-', '%', "' OR 1=1--"]):
        response = _signup({'username': f'tryer{i}', 'college_code': code, 'level': 3}, f'10.6.0.{i}')
        if code.strip().lower() == 'cs':
            assert response.status_code == 200
        elif code == '':
            assert response.status_code == 200 and response.json()['user']['college'] is False   # just not a student
        else:
            assert response.status_code == 403, code
    assert _signup({'username': 'upper', 'college_code': 'CS', 'level': 2}, '10.6.1.1').json()['user']['college'] is True
    outsider = users('outsider2')
    for code in ['wrong', '', 'c', None]:
        assert outsider.post('/api/account/college', json={'code': code, 'level': 2}).status_code == 403
    assert outsider.get('/api/state').json()['archive'] is False
