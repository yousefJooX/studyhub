"""Exams + study plan, flashcards, the calendar feed, the competitive-programming tracker, course sharing
and the college source adapters. Codeforces, LeetCode, the model and college feeds are local fakes."""
import json
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler

import pytest
from fastapi.testclient import TestClient

from studyhub.app import app

from .conftest import _serve


def day(n):
    return (date.today() + timedelta(days=n)).isoformat()


class JSONHandler(BaseHTTPRequestHandler):
    def send(self, code, data):
        out = json.dumps(data).encode()
        self.send_response(code); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(out)

    def log_message(self, *args):
        pass


# ---------------------------------------------------------------- exams and the study plan

def test_exam_crud_and_validation(personal):
    course = personal.post('/api/courses', json={'title': 'Algorithms'}).json()
    assert personal.post('/api/exams', json={'date': day(5)}).status_code == 400            # no course and no name
    assert personal.post('/api/exams', json={'title': 'x', 'date': '2026-13-01'}).status_code == 422
    assert personal.post('/api/exams', json={'title': 'x', 'date': day(3), 'time': '9am'}).status_code == 422
    exam = personal.post('/api/exams', json={'course_id': course['id'], 'date': day(5), 'time': '09:00', 'weight': 5}).json()
    assert personal.put(f"/api/exams/{exam['id']}", json={'course_id': course['id'], 'date': day(6), 'kind': 'midterm'}).json()['kind'] == 'midterm'
    listed = personal.get('/api/exams').json()['exams']
    assert listed[0]['label'] == 'Algorithms · ميدترم' and personal.get('/api/state').json()['exams'][0]['id'] == exam['id']
    assert personal.delete(f"/api/exams/{exam['id']}").json() == {'ok': True}
    assert personal.delete(f"/api/exams/{exam['id']}").status_code == 404


def test_study_plan_fills_days_and_reviews_before_each_exam(personal):
    near = personal.post('/api/exams', json={'title': 'Physics', 'date': day(4), 'weight': 4}).json()
    far = personal.post('/api/exams', json={'title': 'History', 'date': day(8), 'weight': 2}).json()
    preview = personal.post('/api/exams/plan', json={'dry_run': True, 'minutes_per_day': 100, 'session_minutes': 50}).json()
    assert preview['sessions'] == 15 and preview['first'] == day(0) and preview['last'] == day(7)
    by_day = {}
    for item in preview['preview']:
        by_day.setdefault(item['date'], []).append(item)
    assert {i['exam_id'] for i in by_day[day(3)]} == {near['id']} and all(i['kind'] == 'review' for i in by_day[day(3)])
    assert len(by_day[day(4)]) == 1 and by_day[day(4)][0]['exam_id'] == far['id']      # exam day: half
    assert all(i['exam_id'] == far['id'] for d in (day(5), day(6), day(7)) for i in by_day[d])
    assert personal.get('/api/state').json()['tasks'] == []                             # a preview writes nothing
    made = personal.post('/api/exams/plan', json={'minutes_per_day': 100, 'session_minutes': 50, 'lang': 'en'}).json()
    tasks = personal.get('/api/state').json()['tasks']
    assert made['created'] == 15 and len(tasks) == 15 and all(t['plan'] and t['duration_minutes'] == 50 for t in tasks)
    assert any(t['title'] == 'Final review: Physics' for t in tasks)
    done = tasks[0]
    personal.put(f"/api/tasks/{done['id']}", json={**{k: done[k] for k in ('title', 'planned_date')}, 'status': 'done'})
    again = personal.post('/api/exams/plan', json={'minutes_per_day': 50, 'session_minutes': 50, 'rest_days': [(date.today().weekday() + 2) % 7]}).json()
    tasks = personal.get('/api/state').json()['tasks']
    assert again['removed'] == 14 and done['id'] in {t['id'] for t in tasks}                # finished sessions stay
    assert personal.get('/api/exams/plan/settings').json()['minutes_per_day'] == 50
    assert personal.delete('/api/exams/plan').json()['removed'] == len(tasks) - 1


def test_plan_needs_a_future_exam(personal):
    personal.post('/api/exams', json={'title': 'Old', 'date': day(-3)})
    assert personal.post('/api/exams/plan', json={}).status_code == 400


# ---------------------------------------------------------------- flashcards

def test_cards_spaced_repetition(personal):
    course = personal.post('/api/courses', json={'title': 'ML'}).json()
    added = personal.post('/api/cards', json={'cards': [{'front': 'Bias?', 'back': 'Underfitting', 'course_id': course['id']},
                                                        {'front': 'bias?', 'back': 'dup', 'course_id': course['id']}]}).json()
    assert added['added'] == 1 and added['skipped'] == 1
    card = added['cards'][0]
    assert personal.get('/api/state').json()['cards_due'] == 1
    intervals = [personal.post(f"/api/cards/{card['id']}/review", json={'grade': g}).json()['interval'] for g in ('good', 'good', 'good', 'easy')]
    assert intervals[:2] == [1, 3] and intervals[2] >= 7 and intervals[3] > intervals[2]
    lapsed = personal.post(f"/api/cards/{card['id']}/review", json={'grade': 'again'}).json()
    assert lapsed['interval'] == 0 and lapsed['due'] == date.today().isoformat() and lapsed['lapses'] == 1 and lapsed['ease'] < 2.5
    assert personal.get('/api/cards').json()['reviewed_today'] == 5
    assert personal.post(f"/api/cards/{card['id']}/review", json={'grade': 'meh'}).status_code == 422
    assert personal.put(f"/api/cards/{card['id']}", json={'front': 'Bias', 'back': 'High bias = underfit'}).json()['back'] == 'High bias = underfit'
    assert personal.post('/api/cards', json={'cards': [{'front': 'a', 'back': 'b', 'course_id': 'nope'}]}).status_code == 404
    personal.delete(f"/api/courses/{course['id']}")
    assert personal.get('/api/cards').json()['cards'][0]['course_id'] == ''                 # detached, not lost
    assert personal.delete(f"/api/cards/{card['id']}").json() == {'ok': True}


def test_cards_extracted_from_obsidian_syntax(personal):
    note = personal.post('/api/notes', json={'title': 'Stacks', 'content': '---\ntags: x\n---\n# Stacks\nStack order :: LIFO\nQueue ::: FIFO\n'
                                                                           '%% hidden :: no %%\n\nWhat is recursion?\n?\nA function that calls itself.\n'}).json()
    cards = personal.post('/api/cards/extract', json={'note_id': note['id']}).json()['cards']
    assert cards == [{'front': 'Stack order', 'back': 'LIFO'}, {'front': 'Queue', 'back': 'FIFO'}, {'front': 'FIFO', 'back': 'Queue'},
                     {'front': 'What is recursion?', 'back': 'A function that calls itself.'}]
    assert personal.post('/api/cards/extract', json={}).status_code == 400


@pytest.fixture
def fake_model(env):
    class Handler(JSONHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            assert 'tools' not in body and body['response_format'] == {'type': 'json_object'}
            cards = [{'front': f'Q{i}', 'back': f'A{i}'} for i in range(12)]
            self.send(200, {'choices': [{'message': {'content': 'Sure! ' + json.dumps({'cards': cards})}}]})

    server = _serve(Handler)
    env.setenv('STUDY_LLM_BASE_URL', f'http://127.0.0.1:{server.server_port}')
    env.setenv('STUDY_LLM_MODEL', 'test-model')
    yield server
    server.shutdown()


def test_cards_generated_by_the_model(personal, fake_model):
    note = personal.post('/api/notes', json={'title': 'Long', 'content': 'Gradient descent moves the weights against the gradient. ' * 5}).json()
    result = personal.post('/api/cards/generate', json={'note_id': note['id'], 'count': 5}).json()
    assert result['cards'] == [{'front': f'Q{i}', 'back': f'A{i}'} for i in range(5)] and result['source'] == f"note:{note['id']}"
    short = personal.post('/api/notes', json={'title': 'Short', 'content': 'tiny'}).json()
    assert personal.post('/api/cards/generate', json={'note_id': short['id']}).status_code == 400


def test_cards_from_a_vault_note_get_its_course(personal, tmp_path, env):
    vault = tmp_path / 'vault'
    (vault / 'ML' / 'week1').mkdir(parents=True)
    (vault / 'ML' / 'week1' / 'loss.md').write_text('MSE :: mean squared error\n')
    env.setenv('STUDY_VAULT_ROOT', str(vault))
    personal.post('/api/vault/mounts', json={'folder': 'ML'})
    course = personal.post('/api/courses', json={'title': 'ML', 'vault_folder': 'ML'}).json()
    result = personal.post('/api/cards/extract', json={'path': 'ML/week1/loss.md'}).json()
    assert result['course_id'] == course['id'] and result['cards'] == [{'front': 'MSE', 'back': 'mean squared error'}]
    assert personal.post('/api/cards/extract', json={'path': '../../etc/passwd'}).status_code in (400, 404)


# ---------------------------------------------------------------- calendar feed

def test_calendar_feed(personal):
    course = personal.post('/api/courses', json={'title': 'Signals, Systems'}).json()
    due = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    personal.post('/api/tasks', json={'title': 'Lab; report', 'course_id': course['id'], 'due': due})
    personal.post('/api/tasks', json={'title': 'Old done', 'due': due, 'status': 'done'})
    personal.post('/api/tasks', json={'title': 'Planned', 'planned_date': day(1), 'planned_time': '18:30', 'duration_minutes': 45})
    personal.post('/api/slots', json={'title': 'Lecture', 'day': 2, 'start': '10:00', 'end': '12:00', 'location': 'Hall A'})
    personal.post('/api/exams', json={'course_id': course['id'], 'date': day(9)})
    assert personal.get('/api/calendar').json()['active'] is False
    link = personal.post('/api/calendar/token?lang=en').json()
    assert link['url'].endswith('.ics') and link['webcal'].startswith('webcal://')
    feed = TestClient(app).get('/calendar/' + link['url'].rsplit('/', 1)[1])   # no cookie: the link alone is enough
    text = feed.text
    assert feed.status_code == 200 and feed.headers['content-type'].startswith('text/calendar')
    assert text.startswith('BEGIN:VCALENDAR\r\n') and text.endswith('END:VCALENDAR\r\n') and text.count('BEGIN:VEVENT') == 4
    assert 'Lab\\; report · Signals\\, Systems' in text and 'Old done' not in text
    assert 'DTSTART;TZID=Africa/Cairo:' + day(1).replace('-', '') + 'T183000' in text and 'RRULE:FREQ=WEEKLY' in text
    assert 'LOCATION:Hall A' in text and 'Exam: Signals\\, Systems · Final' in text
    assert all(len(line.encode()) <= 75 for line in text.split('\r\n'))
    personal.post('/api/calendar/token')                                       # a new link stops the old one
    assert TestClient(app).get('/calendar/' + link['url'].rsplit('/', 1)[1]).status_code == 404
    assert TestClient(app).get('/calendar/nothing.ics').status_code == 404
    personal.delete('/api/calendar/token')
    assert personal.get('/api/calendar').json()['active'] is False


def test_calendar_feed_is_per_user(users):
    ana, bob = users('ana'), users('bob')
    ana.post('/api/tasks', json={'title': 'Ana secret', 'due': (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()})
    bob.post('/api/tasks', json={'title': 'Bob thing', 'due': (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()})
    url = bob.post('/api/calendar/token').json()['url']
    text = TestClient(app).get('/calendar/' + url.rsplit('/', 1)[1]).text
    assert 'Bob thing' in text and 'Ana secret' not in text


# ---------------------------------------------------------------- competitive programming

@pytest.fixture
def fake_cp(env):
    now = datetime.now(timezone.utc)
    yesterday = int((now - timedelta(days=1)).timestamp())

    class Handler(JSONHandler):
        def do_GET(self):
            path, _, query = self.path.partition('?')
            params = dict(p.split('=', 1) for p in query.split('&') if '=' in p)
            if path == '/api/user.info':
                if params['handles'] != 'alice':
                    return self.send(400, {'status': 'FAILED', 'comment': 'handles: User with handle x not found'})
                return self.send(200, {'status': 'OK', 'result': [{'handle': 'alice', 'rating': 1420, 'maxRating': 1500, 'rank': 'specialist', 'maxRank': 'specialist'}]})
            if path == '/api/user.status':
                subs = [{'creationTimeSeconds': yesterday - 86400 * i, 'verdict': 'OK', 'problem': {'contestId': 100 + i, 'index': 'A', 'name': f'P{i}', 'rating': 1200, 'tags': ['math', 'greedy']}} for i in range(3)]
                subs.append({'creationTimeSeconds': yesterday, 'verdict': 'WRONG_ANSWER', 'problem': {'contestId': 999, 'index': 'B', 'name': 'Hard', 'tags': []}})
                subs.append({'creationTimeSeconds': yesterday, 'verdict': 'OK', 'problem': {'contestId': 100, 'index': 'A', 'name': 'P0', 'tags': ['math']}})
                return self.send(200, {'status': 'OK', 'result': subs})
            if path == '/api/contest.list':
                return self.send(200, {'status': 'OK', 'result': [
                    {'id': 2001, 'name': 'Codeforces Round (Div. 2)', 'phase': 'BEFORE', 'startTimeSeconds': int((now + timedelta(days=2)).timestamp()), 'durationSeconds': 7200},
                    {'id': 1999, 'name': 'Old round', 'phase': 'FINISHED', 'startTimeSeconds': int((now - timedelta(days=9)).timestamp()), 'durationSeconds': 7200}]})
            if path == '/api/problemset.problems':
                return self.send(200, {'status': 'OK', 'result': {'problems': [{'contestId': 300 + i, 'index': 'C', 'name': f'Daily{i}', 'rating': 1500} for i in range(4)]}})
            self.send(404, {})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if 'upcomingContests' in body['query']:
                return self.send(200, {'data': {'activeDailyCodingChallengeQuestion': {'date': day(0), 'link': '/problems/two-sum/', 'question': {'title': 'Two Sum', 'difficulty': 'Easy'}},
                                                'upcomingContests': [{'title': 'Weekly Contest 1', 'titleSlug': 'weekly-contest-1', 'startTime': int((now + timedelta(days=1)).timestamp()), 'duration': 5400}]}})
            if body['variables']['u'] != 'bob_lc':
                return self.send(200, {'data': {'matchedUser': None}, 'errors': [{'message': 'That user does not exist.'}]})
            self.send(200, {'data': {'matchedUser': {'submitStatsGlobal': {'acSubmissionNum': [{'difficulty': 'All', 'count': 30}, {'difficulty': 'Easy', 'count': 20}, {'difficulty': 'Medium', 'count': 9}, {'difficulty': 'Hard', 'count': 1}]},
                                                     'userCalendar': {'streak': 4, 'totalActiveDays': 40}},
                                     'userContestRanking': {'rating': 1612.4, 'attendedContestsCount': 3},
                                     'recentAcSubmissionList': [{'title': 'Two Sum', 'titleSlug': 'two-sum', 'timestamp': str(yesterday)}]}})

    server = _serve(Handler)
    env.setenv('STUDY_CF_API', f'http://127.0.0.1:{server.server_port}/api')
    env.setenv('STUDY_LC_API', f'http://127.0.0.1:{server.server_port}/graphql')
    yield server
    server.shutdown()


def test_cp_tracker(personal, fake_cp):
    empty = personal.get('/api/cp').json()
    assert empty['codeforces'] is None and [c['platform'] for c in empty['contests']] == ['LeetCode', 'Codeforces']
    assert empty['daily']['leetcode']['url'] == 'https://leetcode.com/problems/two-sum/'
    assert personal.put('/api/cp', json={'codeforces': 'ghost'}).status_code == 400
    assert personal.get('/api/cp').json()['handles']['codeforces'] == ''               # a wrong handle is not kept
    assert personal.put('/api/cp', json={'codeforces': 'a b'}).status_code == 422
    data = personal.put('/api/cp', json={'codeforces': '@alice', 'leetcode': 'bob_lc'}).json()
    cf, lc = data['codeforces'], data['leetcode']
    assert cf['rating'] == 1420 and cf['solved'] == 3 and cf['streak'] == 3 and cf['top_tags'][0] == ['math', 3] and 'solved_keys' not in cf
    assert lc['solved'] == 30 and lc['hard'] == 1 and lc['rating'] == 1612 and lc['streak'] == 4
    assert data['daily']['codeforces']['rating'] == 1500 and data['daily']['codeforces']['name'].startswith('Daily')
    assert personal.get('/api/cp').json()['daily']['codeforces'] == data['daily']['codeforces']   # same problem all day
    url = personal.post('/api/calendar/token').json()['url']
    feed = TestClient(app).get('/calendar/' + url.rsplit('/', 1)[1]).text
    assert 'Codeforces Round (Div. 2)' in feed and 'Weekly Contest 1' in feed and 'Old round' not in feed
    personal.put('/api/cp', json={'codeforces': 'alice', 'leetcode': 'bob_lc', 'contests_in_calendar': False})
    assert 'Weekly Contest 1' not in TestClient(app).get('/calendar/' + url.rsplit('/', 1)[1]).text


def test_cp_keeps_last_numbers_when_offline(personal, fake_cp, env):
    personal.put('/api/cp', json={'codeforces': 'alice'})
    fake_cp.shutdown()
    fake_cp.server_close()
    data = personal.get('/api/cp?refresh=true').json()
    assert data['errors'] == {'codeforces': 'network'} and data['codeforces']['rating'] == 1420


# ---------------------------------------------------------------- sharing a course

def test_share_a_course_with_a_friend(users):
    owner, friend = users('owner'), users('friend')
    course = owner.post('/api/courses', json={'title': 'Robotics'}).json()
    owner.post('/api/notes', json={'title': 'Kinematics', 'course_id': course['id'], 'content': '# DH params'})
    owner.post('/api/tasks', json={'title': 'Lab 1', 'course_id': course['id']})
    owner.post('/api/tasks', json={'title': 'Done one', 'course_id': course['id'], 'status': 'done'})
    owner.post('/api/slots', json={'title': 'Lab', 'course_id': course['id'], 'day': 1, 'start': '09:00', 'end': '11:00'})
    owner.post('/api/exams', json={'course_id': course['id'], 'date': day(7)})
    owner.post('/api/exams', json={'course_id': course['id'], 'date': day(-7), 'kind': 'quiz'})
    owner.post('/api/cards', json={'cards': [{'front': 'DOF?', 'back': 'Degrees of freedom', 'course_id': course['id']}]})
    owner.post('/api/notes', json={'title': 'Other course note', 'content': 'not shared'})
    assert owner.get(f"/api/courses/{course['id']}/share").json() == {'active': False}
    code = owner.post(f"/api/courses/{course['id']}/share").json()['code']
    assert len(code) == 8 and owner.get(f"/api/courses/{course['id']}/share").json()['code'] == code
    preview = friend.get(f'/api/shares/{code.lower()[:4]}-{code[4:]}').json()
    assert preview == {'course': 'Robotics', 'owner': 'Owner', 'own': False, 'counts': {'notes': 1, 'tasks': 1, 'slots': 1, 'exams': 1, 'cards': 1}}
    assert owner.get(f'/api/shares/{code}').json()['own'] is True
    assert owner.post(f'/api/shares/{code}/import').status_code == 400
    imported = friend.post(f'/api/shares/{code}/import').json()
    state = friend.get('/api/state').json()
    assert [c['title'] for c in state['courses']] == ['Robotics'] and state['courses'][0]['shared_by'] == 'Owner'
    assert [n['title'] for n in state['notes']] == ['Kinematics'] and [t['title'] for t in state['tasks']] == ['Lab 1']
    assert len(state['slots']) == 1 and len(state['exams']) == 1 and state['cards_due'] == 1
    assert all(x['course_id'] == imported['course_id'] for x in state['notes'] + state['tasks'] + state['slots'] + state['exams'])
    friend.post('/api/notes', json={'title': 'Friend private', 'content': 'mine'})
    assert 'Friend private' not in [n['title'] for n in owner.get('/api/state').json()['notes']]   # copies, not a link
    owner.delete(f"/api/courses/{course['id']}/share")
    assert friend.get(f'/api/shares/{code}').status_code == 404


def test_share_is_friends_site_only(personal):
    course = personal.post('/api/courses', json={'title': 'Solo'}).json()
    assert personal.post(f"/api/courses/{course['id']}/share").status_code == 404
    assert personal.get('/api/shares/ABCDEFGH').status_code == 404


def test_share_code_guessing_is_limited(users):
    guesser = users('guesser')
    codes = [guesser.get('/api/shares/ABCDEFG' + c).status_code for c in 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789ABCDEFGHJK']
    assert codes[:40] == [404] * 40 and codes[-1] == 429


# ---------------------------------------------------------------- college source adapters

def test_json_feed_adapter(users, env):
    deadline = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    seen = []

    class Handler(JSONHandler):
        def do_GET(self):
            seen.append(self.path)
            self.send(200, {'posts': [{'id': 'a1', 'kind': 'assignment', 'title': 'Sheet 3', 'course_code': 'EE201', 'course_name': 'Circuits',
                                       'deadline': deadline, 'url': 'https://example.edu/a1'},
                                      {'id': 'n1', 'kind': 'announcement', 'title': 'Midterm moved', 'posted': day(0), 'url': 'javascript:alert(1)'}],
                            'courses': [{'code': 'EE201', 'name': 'Circuits'}]})

    server = _serve(Handler)
    env.setenv('STUDY_COLLEGE_SOURCE', 'json_feed')
    env.setenv('STUDY_COLLEGE_SOURCE_NAME', 'EE channel')
    env.setenv('STUDY_COLLEGE_FEED_URL', f'http://127.0.0.1:{server.server_port}/feed/{{level}}/{{program}}.json')
    env.setenv('STUDY_COLLEGE_PROGRAMS', 'power=Power Engineering;comm=Communications')
    env.setenv('STUDY_COLLEGE_LEVELS', '5')
    env.delenv('STUDY_BOARD_SYNC', raising=False)
    auth = TestClient(app).get('/api/auth').json()
    assert auth['programs'] == {'power': 'Power Engineering', 'comm': 'Communications'} and auth['levels'] == 5
    student = users('eng', college_code='cs', level=5, program='comm')
    result = student.post('/api/board/sync').json()
    assert result['added'] == 1 and seen[0] == '/feed/5/comm.json'
    task = student.get('/api/state').json()['tasks'][0]
    assert task['title'] == 'Sheet 3' and task['board']['course'] == 'EE201 · Circuits' and 'عبر EE channel: https://example.edu/a1' in task['details']
    news = student.get('/api/board/announcements').json()['items']
    assert [n['title'] for n in news] == ['Midterm moved'] and news[0]['url'] == ''            # unsafe link dropped
    assert student.get('/api/board').json()['source'] == 'EE channel'
    server.shutdown()


def test_college_levels_and_programs_are_validated(users, env):
    env.setenv('STUDY_COLLEGE_PROGRAMS', 'power=Power')
    client = TestClient(app)
    bad_program = client.post('/api/signup', json={'name': 'X', 'username': 'xx1', 'password': 'goodpass99', 'college_code': 'cs', 'level': 2, 'program': 'rse'})
    bad_level = client.post('/api/signup', json={'name': 'X', 'username': 'xx2', 'password': 'goodpass99', 'college_code': 'cs', 'level': 5, 'program': 'power'})
    assert bad_program.status_code == 400 and bad_level.status_code == 400


def test_no_college_source(users, env):
    env.setenv('STUDY_COLLEGE_SOURCE', 'none')
    student = users('quiet', college_code='cs', level=2)
    assert student.get('/api/board').json()['available'] is False
    assert student.get('/api/board/announcements').json() == {'items': [], 'available': False}
