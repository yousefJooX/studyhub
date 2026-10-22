"""Every API of the single-owner (personal) site, end to end through HTTP."""
import io
import json
import sqlite3
import time
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from studyhub.app import app

from .conftest import OWNER_PASSWORD


def test_public_pages(env):
    client = TestClient(app)
    assert client.get('/health').json()['status'] == 'ok'
    home = client.get('/')
    assert home.status_code == 200 and 'StudyHub' in home.text and '__ORIGIN__' not in home.text
    assert client.get('/sw.js').status_code == 200
    assert client.get('/manifest.webmanifest').json()['short_name'] == 'StudyHub'
    assert client.get('/static/app.js').status_code == 200
    assert client.get('/api/auth').json() == {'multiuser': False, 'signup': False, 'invite': False, 'college_code': False,
                                              'college_name': client.get('/api/auth').json()['college_name'],
                                              'programs': client.get('/api/auth').json()['programs'], 'authenticated': False}


def test_link_preview_uses_public_origin(env):
    env.setenv('STUDY_PUBLIC_ORIGIN', 'https://study.example.com')
    html = TestClient(app).get('/').text
    assert 'content="https://study.example.com/static/icon-512.png"' in html
    assert '<title>مساحتك الدراسية — StudyHub</title>' in html and 'يوسف' not in html


def test_login_logout(env):
    from .conftest import _OWNER_HASH
    env.setenv('STUDY_PASSWORD_HASH', _OWNER_HASH)
    client = TestClient(app)
    assert client.get('/api/state').status_code == 401
    assert client.post('/api/login', json={'password': 'wrong'}).status_code == 401
    assert client.post('/api/login', json={'password': OWNER_PASSWORD}).status_code == 200
    assert client.get('/api/state').status_code == 200
    assert client.post('/api/logout').status_code == 200
    assert client.get('/api/state').status_code == 401


def test_state_shape(personal):
    state = personal.get('/api/state').json()
    for key in ('courses', 'tasks', 'slots', 'notes', 'uploads', 'user', 'archive', 'college', 'task_defaults'):
        assert key in state, key
    assert state['archive'] is True and state['college']['college'] is True


def test_courses_crud(personal):
    course = personal.post('/api/courses', json={'title': 'Robotics', 'kind': 'college'}).json()
    assert course['title'] == 'Robotics'
    edited = personal.put(f"/api/courses/{course['id']}", json={'title': 'Robotics 2', 'kind': 'college'}).json()
    assert edited['title'] == 'Robotics 2'
    task = personal.post('/api/tasks', json={'title': 't', 'course_id': course['id']}).json()
    assert personal.delete(f"/api/courses/{course['id']}").json()['detached']['tasks'] == 1
    left = personal.get('/api/state').json()
    assert left['courses'] == [] and left['tasks'][0]['course_id'] == ''  # the task survives, detached
    assert personal.post('/api/courses', json={'title': ''}).status_code == 422
    assert personal.post('/api/tasks', json={'title': 'x', 'course_id': 'missing'}).status_code in (400, 404)
    assert task['id']


def test_tasks_crud_and_validation(personal):
    task = personal.post('/api/tasks', json={'title': 'Sheet 1', 'due': '2030-01-01T10:00:00+02:00', 'priority': 'high',
                                             'planned_date': '2029-12-30', 'duration_minutes': 45}).json()
    assert task['duration_minutes'] == 45 and task['status'] == 'todo'
    updated = personal.put(f"/api/tasks/{task['id']}", json={**task, 'status': 'done'}).json()
    assert updated['status'] == 'done'
    assert personal.post('/api/tasks', json={'title': 'x', 'due': '2030-01-01T10:00'}).status_code == 422  # needs timezone
    assert personal.post('/api/tasks', json={'title': 'x', 'priority': 'urgent'}).status_code == 422
    assert personal.post('/api/tasks', json={'title': 'x', 'planned_time': '10:00'}).status_code == 400
    assert personal.delete(f"/api/tasks/{task['id']}").status_code == 200
    assert personal.get('/api/state').json()['tasks'] == []
    assert personal.delete('/api/tasks/nope').status_code == 404


def test_default_task_duration(personal):
    assert personal.put('/api/preferences/tasks', json={'duration_minutes': 50}).status_code == 200
    assert personal.post('/api/tasks', json={'title': 'x'}).json()['duration_minutes'] == 50


def test_slots(personal):
    slot = personal.post('/api/slots', json={'title': 'Lab', 'kind': 'lab', 'day': 1, 'start': '10:00', 'end': '12:00'}).json()
    assert personal.post('/api/slots', json={'title': 'Bad', 'day': 1, 'start': '12:00', 'end': '10:00'}).status_code == 400
    assert personal.post('/api/slots', json={'title': 'Bad', 'day': 9, 'start': '10:00', 'end': '11:00'}).status_code == 422
    assert personal.put(f"/api/slots/{slot['id']}", json={**slot, 'title': 'Lab 2'}).json()['title'] == 'Lab 2'
    assert personal.delete(f"/api/slots/{slot['id']}").status_code == 200


def test_notes_and_markdown(personal):
    note = personal.post('/api/notes', json={'title': 'N', 'content': '# Hi\n$x^2$\n> [!tip] T\n> body\n<script>x</script>'}).json()
    full = personal.get(f"/api/notes/{note['id']}").json()
    assert '<h1' in full['html'] and 'callout' in full['html'] and '<script>' not in full['html']
    assert personal.put(f"/api/notes/{note['id']}", json={'title': 'N2', 'content': 'x'}).json()['title'] == 'N2'
    assert personal.delete(f"/api/notes/{note['id']}").status_code == 200


def test_uploads(personal):
    up = personal.post('/api/uploads?name=lecture.pdf', content=b'%PDF-1.4 hello').json()
    got = personal.get(f"/api/uploads/{up['id']}")
    assert got.content == b'%PDF-1.4 hello' and 'inline' in got.headers['content-disposition']
    html = personal.post('/api/uploads?name=page.html', content=b'<script>1</script>').json()
    assert 'attachment' in personal.get(f"/api/uploads/{html['id']}").headers['content-disposition']
    assert personal.post('/api/uploads?name=.hidden', content=b'x').status_code == 400
    assert personal.delete(f"/api/uploads/{up['id']}").status_code == 200
    assert personal.get(f"/api/uploads/{up['id']}").status_code == 404


def test_timer_records_focus_time(personal, tmp_path):
    task = personal.post('/api/tasks', json={'title': 'Focus', 'duration_minutes': 30}).json()
    started = personal.post(f"/api/tasks/{task['id']}/timer", json={'action': 'start'}).json()
    assert started['timer']['state'] == 'running' and started['status'] == 'doing'
    db = sqlite3.connect(tmp_path / 'data' / 'study.sqlite3')
    data = json.loads(db.execute('SELECT data FROM tasks').fetchone()[0])
    data['timer']['segment_start'] -= 600
    db.execute('UPDATE tasks SET data=?', (json.dumps(data),)); db.commit()
    assert personal.get('/api/weekly').json()['focus_minutes'] == 10                    # live estimate
    paused = personal.post(f"/api/tasks/{task['id']}/timer", json={'action': 'pause'}).json()
    assert paused['timer']['state'] == 'paused'
    assert db.execute('SELECT COUNT(*) FROM study_sessions').fetchone()[0] == 1          # saved for real
    assert personal.post(f"/api/tasks/{task['id']}/timer", json={'action': 'reset'}).json()['timer']['state'] == 'idle'
    assert personal.post(f"/api/tasks/{task['id']}/timer", json={'action': 'configure', 'duration_minutes': 15}).json()['duration_minutes'] == 15
    assert personal.post(f"/api/tasks/{task['id']}/timer", json={'action': 'explode'}).status_code == 422


def test_finished_timer_is_saved_by_worker_and_on_edit(personal, tmp_path):
    from studyhub.app import settle_timers
    a = personal.post('/api/tasks', json={'title': 'A'}).json()
    b = personal.post('/api/tasks', json={'title': 'B'}).json()
    for t in (a, b):
        personal.post(f"/api/tasks/{t['id']}/timer", json={'action': 'start'})
    db = sqlite3.connect(tmp_path / 'data' / 'study.sqlite3')
    for row_id, raw in db.execute('SELECT id, data FROM tasks').fetchall():
        data = json.loads(raw)
        data['timer']['segment_start'] -= 1600
        data['timer']['ends_at'] -= 1600
        db.execute('UPDATE tasks SET data=? WHERE id=?', (json.dumps(data), row_id))
    db.commit()
    bt = [t for t in personal.get('/api/state').json()['tasks'] if t['id'] == b['id']][0]
    personal.put(f"/api/tasks/{b['id']}", json={**bt, 'duration_minutes': 45})            # edit after it ran out
    settle_timers()                                                                       # worker tick
    assert db.execute('SELECT COUNT(*) FROM study_sessions').fetchone()[0] == 2
    assert personal.get('/api/weekly').json()['focus_minutes'] == 50


def test_weekly_review(personal):
    task = personal.post('/api/tasks', json={'title': 'Done thing'}).json()
    personal.put(f"/api/tasks/{task['id']}", json={**task, 'status': 'done'})
    weekly = personal.get('/api/weekly').json()
    assert weekly['done_count'] == 1 and len(weekly['days']) == 7
    assert personal.get('/api/weekly?weeks_ago=1').status_code == 200


def _vault(tmp_path):
    root = tmp_path / 'vault'
    (root / 'Univ' / 'NLP').mkdir(parents=True)
    (root / 'Univ' / 'NLP' / 'Lecture 1.md').write_text('# Attention\n$$a=b$$\n```python\nx=1\n```')
    (root / 'Univ' / 'NLP' / 'slides.pdf').write_bytes(b'%PDF-1.4')
    (root / 'Secret').mkdir()
    (root / 'Secret' / 'private.md').write_text('nope')
    return root


def test_vault_library_reader(personal, env, tmp_path):
    env.setenv('STUDY_VAULT_ROOT', str(_vault(tmp_path)))
    assert personal.post('/api/vault/mounts', json={'folder': 'Univ'}).status_code == 200
    files = personal.get('/api/vault').json()['files']
    assert {f['name'] for f in files} >= {'Lecture 1.md', 'slides.pdf'}
    data = personal.get('/api/vault/note-data?path=Univ/NLP/Lecture 1.md').json()
    assert 'Attention' in data['html'] and data['folder'] == 'Univ/NLP'
    assert personal.get('/api/vault/note?path=Univ/NLP/Lecture 1.md').status_code == 200
    assert personal.get('/api/vault/file?path=Univ/NLP/slides.pdf').content == b'%PDF-1.4'
    assert personal.get('/api/vault/note-data?path=Secret/private.md').status_code == 404   # not mounted
    assert personal.get('/api/vault/note-data?path=../../etc/passwd').status_code == 404


def test_voices(personal):
    stages = personal.get('/api/voices').json()['stages']
    assert [s['id'] for s in stages] == ['day', 'half-day', 'hour']
    clip = personal.post('/api/voices/day?name=my clip.mp3', content=b'ID3fake').json()
    assert personal.get(clip['url']).headers['content-type'].startswith('audio/')
    assert personal.post('/api/voices/day?name=x.html', content=b'<b>').status_code == 400
    assert personal.post('/api/voices/nope?name=x.mp3', content=b'x').status_code == 404
    assert personal.post('/api/voices/day/test').status_code == 404        # no device subscribed yet
    assert personal.delete(clip['url']).status_code == 200


def test_push_subscription_validation(personal):
    config = personal.get('/api/notifications/push/config').json()
    assert config['public_key']
    bad = {'endpoint': 'https://evil.example.com/x', 'keys': {'p256dh': 'a', 'auth': 'b'}}
    assert personal.post('/api/notifications/push/subscription', json=bad).status_code == 422
    import base64, os as _os
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    point = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    b64 = lambda raw: base64.urlsafe_b64encode(raw).decode().rstrip('=')
    good = {'endpoint': 'https://fcm.googleapis.com/fcm/send/abc', 'keys': {'p256dh': b64(point), 'auth': b64(_os.urandom(16))}}
    fake = {'endpoint': 'https://fcm.googleapis.com/fcm/send/abc', 'keys': {'p256dh': 'BNc' + 'a' * 84, 'auth': 'b' * 22}}
    assert personal.post('/api/notifications/push/subscription', json=fake).status_code == 422   # not a real key
    assert personal.post('/api/notifications/push/subscription', json=good).status_code == 200
    assert personal.get('/api/notifications/push/config').json()['devices'] == 1
    assert personal.request('DELETE', '/api/notifications/push/subscription', json={'endpoint': good['endpoint']}).status_code == 200


def test_deadline_stage_events():
    from datetime import datetime, timedelta
    from studyhub.voices import deadline_events
    now = time.time()
    due = datetime.fromtimestamp(now + 30 * 60).astimezone().isoformat()
    events = deadline_events({'due': due, 'status': 'todo', 'created': now - 86400 * 3}, now)
    assert events and events[0][0] == 'deadline:hour'
    assert deadline_events({'due': due, 'status': 'done', 'created': 0}, now) == []
    # created 30 minutes before the deadline: the "1 hour left" stage was already over, so no stale burst
    assert deadline_events({'due': due, 'status': 'todo', 'created': now}, now) == []


def test_profile_and_board_on_personal_site(personal, fake_board):
    assert personal.put('/api/profile/college', json={'level': 4, 'program': 'rse'}).json() == {'level': 4, 'program': 'rse'}
    assert personal.put('/api/profile/college', json={'level': 9}).status_code == 400
    result = personal.post('/api/board/sync').json()
    assert result['added'] == 3 and result['closed'] == 2


def test_cross_origin_write_rejected(personal):
    assert personal.post('/api/tasks', json={'title': 'x'}, headers={'origin': 'https://evil.example'}).status_code == 403


def test_owner_name(env, personal):
    env.setenv('STUDY_OWNER_NAME', 'Joox')
    assert personal.get('/api/state').json()['user']['name'] == 'Joox'


def test_owner_announcements_need_a_level_first(personal, fake_board):
    """Regression: on the single-owner site the announcements card vanished when no level was saved."""
    first = personal.get('/api/board/announcements').json()
    assert first == {'items': [], 'available': True, 'needs_level': True}
    assert personal.put('/api/profile/college', json={'level': 4, 'program': 'rse'}).status_code == 200
    news = personal.get('/api/board/announcements').json()
    assert news['available'] and len(news['items']) == 4
