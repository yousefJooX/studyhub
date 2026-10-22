"""Record the StudyHub walkthrough videos (docs/walkthrough-*.mp4 + .srt + transcript).

    cd ~/studyhub && .venv/bin/python studyhub/docs/video/record.py            # both videos
    .venv/bin/python studyhub/docs/video/record.py everyone                    # or one of: everyone, fcsit

Needs the dev requirements (Playwright + Chromium) and ffmpeg. Nothing touches the internet or a real
account: a throwaway StudyHub runs on a free local port with demo data, next to small fakes for HNU Board,
the college archive, Codeforces/LeetCode and the AI model.

How it looks: the real app runs inside a browser window on a "stage" page (stage.html). The driver below
clicks and types in the app like a person, moves a visible cursor, zooms the camera onto whatever is being
used and shows a caption. Chrome's screencast captures the stage, ffmpeg turns it into H.264 with the music.
"""
import asyncio
import base64
import io
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACKAGE = HERE.parents[1]                 # the studyhub folder (repository root)
DOCS = PACKAGE / 'docs'
FIXTURES = PACKAGE / 'tests' / 'fixtures' / 'board'
MUSIC = HERE / 'music.m4a'
SITE = 'study-joox-friends.loophole.site'
PASSWORD = 'demo-pass-2026'               # the throwaway demo account typed on screen


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def serve(handler):
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class Quiet(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, body, ctype='application/json'):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(code); self.send_header('Content-Type', ctype); self.end_headers(); self.wfile.write(body)


# ---------------------------------------------------------------- fakes for everything StudyHub calls out to

def fake_board():
    """HNU Board from the captured pages in tests/fixtures (Level 4 · Robotics)."""
    offsets = {'1': timedelta(days=2, hours=5), '2': timedelta(days=5), '8': timedelta(days=6), '3': timedelta(days=-1), '7': timedelta(days=-4)}

    class Handler(Quiet):
        def do_GET(self):
            path, _, query = self.path.partition('?')
            if path == '/':
                page = 'assignments-1.html' if 'type=assignment' in query and 'page=1' in query else None if 'type=assignment' in query else 'main.html'
                return self.reply(200, (FIXTURES / page).read_bytes() if page else b'<html></html>', 'text/html; charset=utf-8')
            if path.endswith('/calendar.ics') and path.split('/')[2] in offsets:
                start = (datetime.now(timezone.utc) + offsets[path.split('/')[2]]).strftime('%Y%m%dT%H%M%SZ')
                return self.reply(200, f'BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nDTSTART:{start}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n'.encode(), 'text/calendar')
            self.reply(404, b'', 'text/plain')
    return serve(Handler)


def fake_cp():
    """Codeforces + LeetCode with a made-up student, so no real person's account is shown."""
    now = datetime.now(timezone.utc)

    def ts(days, hour=14):
        return int((now + timedelta(days=days)).replace(hour=hour, minute=35, second=0).timestamp())

    names = ['Two Buttons', 'Array Splitting', 'Good Subarrays', 'Binary String Sorting', 'Coloring Game', 'Shortest Path Queries',
             'Make It Increasing', 'Prefix Flip', 'Tree Infection', 'Palindrome Partition', 'Knapsack Again', 'Grid Walk']

    class Handler(Quiet):
        def do_GET(self):
            path = self.path.partition('?')[0]
            if path.endswith('user.info'):
                return self.reply(200, {'status': 'OK', 'result': [{'handle': 'nour_codes', 'rating': 1447, 'maxRating': 1512, 'rank': 'specialist', 'maxRank': 'specialist'}]})
            if path.endswith('user.status'):
                subs = []
                for i in range(64):
                    tags = [['math', 'greedy'], ['implementation'], ['dp'], ['graphs', 'shortest paths'], ['greedy', 'sortings'], ['binary search'], ['strings'], ['dp', 'greedy']][i % 8]
                    subs.append({'creationTimeSeconds': ts(-(i // 3), 20) - i * 60, 'verdict': 'OK' if i % 5 else 'WRONG_ANSWER',
                                 'problem': {'contestId': 1900 + i, 'index': 'B', 'name': names[i % len(names)] + (f' {i}' if i >= len(names) else ''), 'rating': 1100 + (i % 6) * 100, 'tags': tags}})
                return self.reply(200, {'status': 'OK', 'result': subs})
            if path.endswith('contest.list'):
                return self.reply(200, {'status': 'OK', 'result': [
                    {'id': 2131, 'name': 'Codeforces Round (Div. 2)', 'phase': 'BEFORE', 'startTimeSeconds': ts(2), 'durationSeconds': 7200},
                    {'id': 2133, 'name': 'Educational Codeforces Round (Rated for Div. 2)', 'phase': 'BEFORE', 'startTimeSeconds': ts(5), 'durationSeconds': 7200},
                    {'id': 2135, 'name': 'Codeforces Round (Div. 3)', 'phase': 'BEFORE', 'startTimeSeconds': ts(9), 'durationSeconds': 8100}]})
            if path.endswith('problemset.problems'):
                return self.reply(200, {'status': 'OK', 'result': {'problems': [{'contestId': 2100 + i, 'index': 'C', 'name': n, 'rating': 1500} for i, n in enumerate(['Balanced Brackets', 'Robot on a Grid', 'Lucky Segments'])]}})
            self.reply(404, {})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if 'upcomingContests' in body['query']:
                return self.reply(200, {'data': {'activeDailyCodingChallengeQuestion': {'date': date.today().isoformat(), 'link': '/problems/merge-intervals/', 'question': {'title': 'Merge Intervals', 'difficulty': 'Medium'}},
                                                 'upcomingContests': [{'title': 'Weekly Contest 471', 'titleSlug': 'weekly-contest-471', 'startTime': ts(3, 2), 'duration': 5400}]}})
            self.reply(200, {'data': {'matchedUser': {'submitStatsGlobal': {'acSubmissionNum': [{'difficulty': 'All', 'count': 186}, {'difficulty': 'Easy', 'count': 92}, {'difficulty': 'Medium', 'count': 81}, {'difficulty': 'Hard', 'count': 13}]},
                                                      'userCalendar': {'streak': 6, 'totalActiveDays': 141}},
                                      'userContestRanking': {'rating': 1688.2, 'attendedContestsCount': 14},
                                      'recentAcSubmissionList': [{'title': 'Merge Intervals', 'titleSlug': 'merge-intervals', 'timestamp': str(ts(-1, 21))}]}})
    return serve(Handler)


def fake_model():
    """An OpenAI-compatible model: accepts keys starting gsk_, adds a task and a timetable slot when asked."""
    class Handler(Quiet):
        def ok(self):
            return self.headers.get('Authorization', '').startswith('Bearer gsk_')

        def do_GET(self):
            return self.reply(200, {'data': [{'id': 'openai/gpt-oss-120b'}]}) if self.ok() else self.reply(401, {'error': {'message': 'bad key'}})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if not self.ok():
                return self.reply(401, {'error': {'message': 'bad key'}})
            thursday = date.today() + timedelta(days=(3 - date.today().weekday()) % 7 or 7)
            if body['messages'][-1]['role'] == 'user':
                message = {'role': 'assistant', 'content': None, 'tool_calls': [
                    {'id': 'c1', 'type': 'function', 'function': {'name': 'create_task', 'arguments': json.dumps(
                        {'title': 'ML assignment 3', 'course': 'Machine Learning', 'due': f'{thursday}T23:00', 'priority': 'high'})}},
                    {'id': 'c2', 'type': 'function', 'function': {'name': 'create_slot', 'arguments': json.dumps(
                        {'title': 'Robotics lab', 'course': 'Robotics', 'day': 'Monday', 'start': '12:00', 'end': '14:00', 'kind': 'lab'})}}]}
            else:
                message = {'role': 'assistant', 'content': 'Done! I added the ML assignment (due Thursday 11 PM) and your Robotics lab on Mondays 12–2.'}
            self.reply(200, {'choices': [{'message': message}]})
    return serve(Handler)


def make_archive(root):
    """A small college archive: Level 4 · Robotics, term 1, with lectures and sheets."""
    (root / 'files').mkdir(parents=True)
    pdf = b'%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n'
    db = sqlite3.connect(root / 'archive.sqlite3')
    db.executescript('CREATE TABLE accounts(key TEXT, meta TEXT, synced_at REAL); CREATE TABLE courses(account TEXT, id TEXT, data TEXT);'
                     'CREATE TABLE items(account TEXT, id TEXT, data TEXT, active INTEGER);')
    db.execute('INSERT INTO accounts VALUES (?,?,?)', ('y4rob1', json.dumps({'key': 'y4rob1', 'year': 4, 'department': 'Robotics', 'term': 1}), time.time()))
    courses = [('c411', 'ROB 411 - Computer Vision'), ('c401', 'ROB 401 - Robot Design'), ('c402', 'ROB 402 - Robot Arms'),
               ('c405', 'ROB 405 - Natural Language Processing'), ('c400', 'COM 400 - Graduation Project 1')]
    for cid, title in courses:
        db.execute('INSERT INTO courses VALUES (?,?,?)', ('y4rob1', cid, json.dumps({'id': cid, 'title': title})))
        for n, (kind, name) in enumerate([('Lectures', 'Lecture 1 - Introduction'), ('Lectures', 'Lecture 2'), ('Lectures', 'Lecture 3'), ('Sheets', 'Sheet 1')]):
            file = f'{cid}-{n}.pdf'
            (root / 'files' / file).write_bytes(pdf)
            db.execute('INSERT INTO items VALUES (?,?,?,1)', ('y4rob1', f'{cid}i{n}', json.dumps(
                {'id': f'{cid}i{n}', 'course_id': cid, 'title': name, 'category': kind, 'links': [{'file': {'path': f'files/{file}', 'name': f'{name}.pdf', 'size': 812_000}}]})))
    db.commit()
    db.close()


VAULT = {
    'Machine Learning/01 Linear regression.md': '''# Linear regression

> [!tip] Idea
> Fit a line that makes the squared error as small as possible.

The model is $\\hat y = w x + b$ and the loss is

$$J(w,b) = \\frac{1}{2m}\\sum_{i=1}^{m} (\\hat y_i - y_i)^2$$

```python
from sklearn.linear_model import LinearRegression
model = LinearRegression().fit(X_train, y_train)
print(model.score(X_test, y_test))
```

```mermaid
flowchart LR
  Data --> Split --> Train --> Evaluate
```

What does the loss measure :: The average squared distance between predictions and labels
Gradient descent moves weights ::: against the gradient

Why scale features before gradient descent?
?
So every feature takes similar steps and the loss surface is not stretched.
''',
    'Machine Learning/02 Classification.md': '# Classification\n\n- Logistic regression\n- KNN\n- Decision trees\n',
    'Computer Vision/Edges and corners.md': '# Edges and corners\n\nSobel filter detects :: edges (image gradient in x and y)\nHarris response :: R = det(M) − k·trace(M)²\n',
    'Computer Vision/Feature matching.md': '# Feature matching\n\nSIFT is invariant to scale and rotation.\n',
    'Robotics/Kinematics.md': '# Forward kinematics\n\n| Joint | θ | d | a | α |\n|---|---|---|---|---|\n| 1 | θ1 | 0.3 | 0 | 90° |\n',
}


def vault_zip():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as z:
        for name, text in VAULT.items():
            z.writestr('My Notes/' + name, text)
    return buffer.getvalue()


# ---------------------------------------------------------------- the StudyHub server (+ the stage page)

def start_studyhub(data, board, cp, model, archive):
    os.environ.update({
        'STUDY_MULTIUSER': '1', 'STUDY_SIGNUP': 'open', 'STUDY_DATA_DIR': str(data), 'STUDY_SECURE_COOKIE': 'false',
        'STUDY_REMINDERS_ENABLED': 'false', 'STUDY_COLLEGE_CODE': 'cs', 'STUDY_MIN_FREE_MB': '1', 'STUDY_RATE_LIMIT': '100000',
        'STUDY_SIGNUPS_PER_IP_HOUR': '1000', 'STUDY_BOARD_URL': f'http://127.0.0.1:{board.server_port}',
        'STUDY_CF_API': f'http://127.0.0.1:{cp.server_port}/api', 'STUDY_LC_API': f'http://127.0.0.1:{cp.server_port}/graphql',
        'STUDY_PROVIDER_BASE_GROQ': f'http://127.0.0.1:{model.server_port}', 'ARCHIVE_DATA_DIR': str(archive),
        'STUDY_LLM_BASE_URL': '', 'STUDY_LLM_API_KEY': '', 'STUDY_PUBLIC_ORIGIN': f'https://{SITE}'})
    sys.path.insert(0, str(PACKAGE.parent))
    import uvicorn
    from studyhub.app import app as studyhub_app
    stage = (HERE / 'stage.html').read_bytes()

    async def recorder(scope, receive, send):
        """The real app, except it may be shown in the stage's frame (recording only, never deployed)."""
        if scope['type'] == 'http' and scope['path'] == '/__stage':
            await send({'type': 'http.response.start', 'status': 200, 'headers': [(b'content-type', b'text/html; charset=utf-8')]})
            return await send({'type': 'http.response.body', 'body': stage})

        async def framed(message):
            if message['type'] == 'http.response.start':
                headers = [(k, v.replace(b"frame-ancestors 'none'", b"frame-ancestors 'self'") if k == b'content-security-policy' else v)
                           for k, v in message['headers'] if k != b'x-frame-options']
                message = message | {'headers': headers}
            await send(message)
        await studyhub_app(scope, receive, framed)

    port = free_port()
    server = uvicorn.Server(uvicorn.Config(recorder, host='127.0.0.1', port=port, log_level='warning'))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        try:
            socket.create_connection(('127.0.0.1', port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    return f'http://127.0.0.1:{port}', server


# ---------------------------------------------------------------- the driver

class Director:
    def __init__(self, page, frames_dir, total_hint):
        self.page, self.frames_dir, self.total_hint = page, frames_dir, total_hint
        self.frames, self.captions, self.t0 = [], [], None
        self.cdp, self.caption_on = None, None

    @property
    def app(self):
        return self.page.frame(name='app')

    async def start(self):
        self.cdp = await self.page.context.new_cdp_session(self.page)
        self.cdp.on('Page.screencastFrame', self._frame)
        await self.cdp.send('Page.startScreencast', {'format': 'jpeg', 'quality': 90, 'maxWidth': 1920, 'maxHeight': 1080, 'everyNthFrame': 1})
        self.t0 = time.time()

    def _frame(self, params):
        n = len(self.frames)
        path = self.frames_dir / f'f{n:06d}.jpg'
        path.write_bytes(base64.b64decode(params['data']))
        self.frames.append((params['metadata']['timestamp'], path))
        asyncio.ensure_future(self.cdp.send('Page.screencastFrameAck', {'sessionId': params['sessionId']}))

    async def stop(self):
        await self.wait(0.6)
        await self.cdp.send('Page.stopScreencast')
        self.close_caption()
        return time.time() - self.t0

    def now(self):
        return time.time() - self.t0

    async def wait(self, seconds):
        await self.page.wait_for_timeout(int(seconds * 1000))
        await self.page.evaluate('f => stage.progress(f)', min(1, self.now() / self.total_hint))

    def close_caption(self):
        if self.caption_on:
            self.captions.append((self.caption_on[0], self.now(), self.caption_on[1], self.caption_on[2]))
            self.caption_on = None

    async def say(self, title, sub='', new='', hold=0.0):
        self.close_caption()
        await self.page.evaluate('([t, s, n]) => stage.caption(t, s, n)', [title, sub, new])
        if title:
            self.caption_on = (self.now(), title, sub)
        if hold:
            await self.wait(hold)

    async def card(self, html, hold):
        await self.page.evaluate('h => stage.card(h)', html)
        await self.wait(hold)

    async def rect(self, selector):
        await self.app.wait_for_selector(selector, state='visible', timeout=15000)
        await self.app.locator(selector).first.scroll_into_view_if_needed()
        return await self.app.locator(selector).first.evaluate('e => { const r = e.getBoundingClientRect(); return {x: r.x, y: r.y, width: r.width, height: r.height}; }')

    async def zoom(self, selector=None, scale=1.6, hold=0.0):
        rect = await self.rect(selector) if selector else None
        await self.page.evaluate('([r, s]) => stage.zoom(r, s)', [rect, scale if selector else 1])
        await self.wait(1.2 + hold)

    async def point(self, selector):
        r = await self.rect(selector)
        x, y = r['x'] + min(r['width'] / 2, 60), r['y'] + r['height'] / 2
        await self.page.evaluate('([x, y]) => stage.point(x, y)', [x, y])
        await self.wait(0.75)
        return x, y

    async def click(self, selector, after=0.8):
        x, y = await self.point(selector)
        await self.page.evaluate('([x, y]) => stage.click(x, y)', [x, y])
        # A real mouse click would miss: the frame sits inside the zoomed camera (a CSS transform).
        await self.app.locator(selector).first.evaluate('e => { e.focus?.(); e.click(); }')
        await self.wait(after)

    async def type(self, selector, text, delay=55):
        await self.click(selector, after=0.2)
        field = self.app.locator(selector).first
        await field.fill('')
        await field.evaluate('e => e.focus()')
        await field.press_sequentially(text, delay=delay)
        await self.wait(0.4)

    async def choose(self, selector, value):
        await self.point(selector)
        await self.app.locator(selector).first.select_option(value)
        await self.wait(0.6)

    async def js(self, code, arg=None):
        return await self.app.evaluate(code, arg)

    async def nav(self, page_name, after=1.2):
        await self.click(f'button.nav[data-page="{page_name}"]', after=after)

    async def scroll(self, selector, block='center'):
        await self.app.locator(selector).first.evaluate(f'e => e.scrollIntoView({{behavior: "smooth", block: "{block}"}})')
        await self.wait(0.9)


# ---------------------------------------------------------------- demo data through the API (as the signed-up user)

async def api(page, base, method, path, data=None, raw=None):
    """Call the API from inside the app frame, with the user's own session (like the app itself does)."""
    frame = page.frame(name='app')
    result = await frame.evaluate("""async ([method, path, data, raw]) => {
        const body = raw ? Uint8Array.from(atob(raw), c => c.charCodeAt(0)) : (data === null ? undefined : JSON.stringify(data));
        const r = await fetch(path, {method, body, credentials: 'same-origin',
            headers: {'content-type': raw ? 'application/zip' : 'application/json'}});
        return {ok: r.ok, status: r.status, text: await r.text()};
    }""", [method, path, data, base64.b64encode(raw).decode() if raw is not None else None])
    if not result['ok']:
        raise RuntimeError(f"{method} {path}: {result['status']} {result['text']}")
    return json.loads(result['text'])


def cairo_iso(day, clock):
    return f'{day}T{clock}:00+03:00'


async def seed_everyone(page, base, data_dir):
    d = lambda n: (date.today() + timedelta(days=n)).isoformat()  # noqa: E731
    ids = {}
    for title in ['Machine Learning', 'Computer Vision', 'Robotics', 'Data Structures']:
        ids[title] = (await api(page, base, 'POST', '/api/courses', {'title': title, 'kind': 'college'}))['id']
    slots = [('Machine Learning', 'lecture', 0, '10:00', '12:00', 'Hall 3'), ('Computer Vision', 'lecture', 1, '12:00', '14:00', 'B-204'),
             ('Robotics', 'section', 2, '09:00', '11:00', 'Lab 2'), ('Data Structures', 'lecture', 3, '10:00', '12:00', 'Hall 1'),
             ('Machine Learning', 'study', 4, '18:00', '20:00', 'Home'), ('Computer Vision', 'lab', 6, '11:00', '13:00', 'Lab 5')]
    for course, kind, day_index, start, end, place in slots:
        await api(page, base, 'POST', '/api/slots', {'title': course, 'course_id': ids[course], 'kind': kind, 'day': day_index, 'start': start, 'end': end, 'location': place})
    tasks = [('Finish CV sheet 2', 'Computer Vision', 1, '23:59', 'high'), ('Read lecture 4 notes', 'Machine Learning', 3, '21:00', 'normal'),
             ('Robot arm report', 'Robotics', 6, '23:59', 'high'), ('Solve 3 graph problems', 'Data Structures', 2, '20:00', 'normal')]
    for title, course, days, clock, priority in tasks:
        await api(page, base, 'POST', '/api/tasks', {'title': title, 'course_id': ids[course], 'due': cairo_iso(d(days), clock), 'priority': priority, 'planned_date': d(0)})
    await api(page, base, 'POST', '/api/exams', {'course_id': ids['Computer Vision'], 'date': d(9), 'time': '09:00', 'weight': 5, 'location': 'Hall 3'})
    await api(page, base, 'POST', '/api/exams', {'course_id': ids['Robotics'], 'date': d(12), 'time': '12:00', 'weight': 3, 'location': 'Hall 1'})
    await api(page, base, 'POST', '/api/exams', {'course_id': ids['Data Structures'], 'date': d(15), 'weight': 4})
    await api(page, base, 'POST', '/api/cards', {'cards': [
        {'front': 'Bias–variance trade-off?', 'back': 'Simple models underfit (high bias); complex ones overfit (high variance).', 'course_id': ids['Machine Learning']},
        {'front': 'Why normalise features before KNN?', 'back': 'Otherwise distances are dominated by the features with the largest scale.', 'course_id': ids['Machine Learning']},
        {'front': 'What is a ROS 2 topic?', 'back': 'A named bus for publish/subscribe messages between nodes.', 'course_id': ids['Robotics']},
        {'front': 'Time complexity of binary search?', 'back': 'O(log n)', 'course_id': ids['Data Structures']}]})
    await api(page, base, 'POST', '/api/vault/upload-zip?name=My%20Notes.zip', raw=vault_zip())
    await api(page, base, 'PUT', f"/api/courses/{ids['Machine Learning']}", {'title': 'Machine Learning', 'kind': 'college', 'vault_folder': 'My Notes/Machine Learning'})
    await api(page, base, 'PUT', f"/api/courses/{ids['Computer Vision']}", {'title': 'Computer Vision', 'kind': 'college', 'vault_folder': 'My Notes/Computer Vision'})
    await focus_history(page, base, data_dir, ids)
    return ids


async def focus_history(page, base, data_dir, ids):
    """Twelve weeks of focus sessions so the weekly review has something to show."""
    user = (await api(page, base, 'GET', '/api/state'))['user']
    db = sqlite3.connect(data_dir / 'users' / user['id'] / 'study.sqlite3')
    courses = list(ids.values())
    now = time.time()
    for days_ago in range(1, 84):
        if (days_ago * 7) % 5 == 0:
            continue
        for n in range(1 + days_ago % 3):
            start = now - days_ago * 86400 + n * 5400 - 30000
            minutes = 25 + (days_ago * 13 + n * 7) % 50
            db.execute('INSERT INTO study_sessions(id,data) VALUES (?,?)', (uuid.uuid4().hex, json.dumps(
                {'task_id': '', 'course_id': courses[(days_ago + n) % len(courses)], 'start': start, 'end': start + minutes * 60, 'minutes': minutes})))
    db.commit()
    db.close()


# ---------------------------------------------------------------- the two scripts

LOGO = '<img src="/static/icon.svg" alt="">'


async def signup(dr, name, username, college=False):
    await dr.click('[data-auth-mode="signup"]')
    await dr.zoom('#signup-form', 1.55)
    await dr.type('#su-name', name)
    await dr.type('#su-user', username)
    await dr.type('#su-pass', PASSWORD, delay=35)
    if college:
        await dr.say('Enter the faculty code', 'It is shared in your class group.')
        await dr.type('#su-college', 'cs', delay=120)
        await dr.wait(0.8)
        await dr.say('Pick your level and program', 'This decides which courses, tasks and announcements you get.')
        await dr.choose('#su-level', '4')
        await dr.choose('#su-program', 'rse')
        await dr.wait(1.2)
    else:
        await dr.point('#su-college')
        await dr.say('Not from the faculty?', 'Leave the code empty. Everything else works the same for you.', hold=2.6)
    await dr.click('#signup-form button[type=submit]', after=1.5)
    await dr.say('')
    await dr.zoom(None)


async def reload_app(dr):
    await dr.js('() => location.reload()')
    await dr.app.wait_for_selector('button.nav[data-page="home"]', timeout=15000)
    await dr.wait(0.8)


async def script_everyone(dr, base, data_dir):
    page = dr.page
    await dr.card(f'{LOGO}<h1>StudyHub</h1><p>A calm place for your courses, tasks, exams and notes.</p>', 3.6)
    await dr.card('', 0.7)
    await dr.say('Start by creating an account', 'Your data is private. Nobody else can see it.')
    await signup(dr, 'Nour Ahmed', 'nour')
    await seed_everyone(page, base, data_dir)
    await reload_app(dr)

    await dr.say('Your overview', 'Today’s tasks, your timetable, exam countdowns and cards to review.', hold=1.4)
    await dr.zoom('.home-strip', 1.5, hold=2.4)
    await dr.zoom(None, hold=0.6)

    await dr.nav('courses')
    await dr.say('Add your courses', 'University courses or anything you study on your own.')
    await dr.click('[data-action="personal-course"]')
    await dr.zoom('#entry-form', 1.5)
    await dr.type('#entry-form input[name="title"]', 'German A2')
    await dr.click('#entry-form button[type=submit]', after=1.4)
    await dr.zoom(None)

    await dr.nav('schedule')
    await dr.say('Your weekly timetable', 'Lectures, sections, labs and your own study sessions.', hold=3.2)

    await dr.nav('tasks')
    await dr.say('Tasks and deadlines', 'Each task has a course, a day to do it and a deadline.', hold=1.2)
    await dr.click('[data-edit-task]')
    await dr.zoom('#entry-form .form-grid', 1.35, hold=0.6)
    await dr.say('You get reminded before the deadline', '1 day, 12 hours and 1 hour before, on your phone and laptop.', hold=2.8)
    await dr.click('#modal [data-action="close"]')
    await dr.zoom(None)

    await dr.nav('exams')
    await dr.say('Your exam timetable', 'Countdowns, and how heavy each exam is.', new='NEW', hold=1.2)
    await dr.click('[data-action="x-exam"]')
    await dr.zoom('#exam-form', 1.45)
    await dr.choose('#exam-form select[name="course_id"]', (await dr.js("() => S.courses.find(c => c.title === 'Machine Learning').id")))
    await dr.choose('#exam-form select[name="kind"]', 'midterm')
    await dr.js("d => { const i = document.querySelector('#exam-form input[name=\"date\"]'); i.value = d; }", (date.today() + timedelta(days=6)).isoformat())
    await dr.choose('#exam-form select[name="weight"]', '4')
    await dr.click('#exam-form button[type=submit]', after=1.2)
    await dr.zoom(None)
    await dr.zoom('.exam-list', 1.3, hold=1.6)
    await dr.zoom(None)
    await dr.say('A study plan in one click', 'Pick your hours per day and rest days.', new='NEW')
    await dr.scroll('.plan-panel', 'start')
    await dr.zoom('#plan-form', 1.45)
    await dr.choose('#plan-form select[name="minutes_per_day"]', '240')
    await dr.click('.rest-days .chip-check:nth-of-type(6)')
    await dr.click('[data-plan="preview"]', after=1.0)
    await dr.zoom('.plan-preview', 1.5)
    await dr.say('Closest and heaviest exams first', 'The day before each exam is its final review.', new='NEW', hold=2.6)
    await dr.click('[data-action="x-plan-make"]', after=1.2)
    await dr.zoom(None)
    await dr.nav('day')
    await dr.say('Sessions land in My day', 'Each one has a focus timer, and your study time is saved.', hold=1.0)
    await dr.zoom('.task-row', 1.45)
    await dr.click('[data-action="timer-start"]', after=2.2)
    await dr.zoom(None)

    await dr.nav('review')
    await dr.say('Flashcards from your notes', 'Already writing “question :: answer” in Obsidian? They are picked up.', new='NEW')
    await dr.click('[data-action="x-cards-from-note"]', after=1.0)
    await dr.zoom('#draft-form', 1.5)
    await dr.choose('#draft-form select[name="source"]', 'path:My Notes/Machine Learning/01 Linear regression.md')
    await dr.click('#draft-form [data-mode="extract"]', after=1.2)
    await dr.zoom('#drafts-save .draft-list', 1.35, hold=1.4)
    await dr.say('Or let the assistant write them', 'You choose which cards to keep.', new='NEW', hold=1.4)
    await dr.click('#drafts-save button[type=submit]', after=1.2)
    await dr.zoom(None)
    await dr.say('Review a few minutes a day', 'Again, Hard, Good or Easy decides when each card comes back, like Anki.', new='NEW')
    await dr.click('[data-action="x-review-start"]', after=0.8)
    await dr.zoom('.review-card', 1.25, hold=1.2)
    await dr.click('[data-action="x-flip"]', after=1.4)
    await dr.click('[data-x="grade"][data-ref="good"]', after=1.0)
    await dr.click('[data-action="x-flip"]', after=1.0)
    await dr.click('[data-x="grade"][data-ref="easy"]', after=0.8)
    await dr.zoom(None)
    await dr.click('[data-action="x-review-end"]', after=0.8)

    await dr.nav('cp', after=1.6)
    await dr.say('Competitive programming', 'Link your Codeforces and LeetCode accounts. No password needed.', new='NEW FOR CS STUDENTS')
    await dr.zoom('#cp-form', 1.5)
    await dr.type('#cp-form input[name="codeforces"]', 'nour_codes')
    await dr.type('#cp-form input[name="leetcode"]', 'nour_codes')
    await dr.click('#cp-form button[type=submit]', after=2.0)
    await dr.zoom(None)
    await dr.zoom('.cp-grid', 1.3)
    await dr.say('Rating, solved problems and your streak', 'Plus the topics you practise most.', new='NEW', hold=2.4)
    await dr.zoom(None)
    await dr.scroll('.contest-row')
    await dr.say('A problem of the day and upcoming contests', 'One click puts a contest in your plan and your calendar.', new='NEW')
    await dr.zoom('.contest-row', 1.5, hold=1.0)
    await dr.click('[data-action="x-contest-task"]', after=1.6)
    await dr.zoom(None)

    await dr.say('The AI assistant', 'The first time, it asks for your own free API key.')
    await dr.click('#assistant-fab', after=1.2)
    await dr.click('[data-scroll="ai-key"]', after=1.6)
    await dr.zoom('#ai-key', 1.5)
    await dr.say('Get a free key from Groq', 'console.groq.com/keys → Create API Key → copy the key starting with gsk_')
    await dr.type('#ai-key input[name="key"]', 'gsk_demo_key_for_the_video', delay=25)
    await dr.click('#ai-key-form button[type=submit]', after=2.0)
    await dr.zoom(None)
    await dr.say('Write like you text a friend', 'It adds tasks and timetable slots for you.')
    await dr.click('#assistant-fab', after=1.0)
    await dr.type('#assistant textarea', 'Add my ML assignment due Thursday 11pm, and a Robotics lab every Monday 12–2', delay=28)
    await dr.click('#assistant [type=submit]', after=3.0)
    await dr.say('Done, all added', 'With deadlines, and linked to the right course.', hold=2.4)
    await dr.click('[data-assistant-close]', after=0.6)

    await dr.nav('library', after=1.4)
    await dr.say('Bring your Obsidian notes', 'Upload your notes folder or a ZIP of it.', hold=2.0)
    await dr.click('[data-vault-note*="Linear regression"]', after=2.0)
    await dr.say('Notes look clean', 'Math, code, callouts, tables and diagrams all render.')
    await dr.zoom('.prose', 1.25, hold=2.6)
    await dr.zoom(None)

    await dr.nav('weekly', after=1.4)
    await dr.say('Weekly review', 'Hours studied, time per course, finished tasks and your streak.', hold=1.0)
    await dr.zoom('.wk-heat, .heatmap, .wk-grid', 1.4, hold=2.0)
    await dr.zoom(None)

    await dr.nav('settings', after=1.4)
    await dr.scroll('#calendar-link')
    await dr.say('Your plan in Google or iPhone Calendar', 'A private link: deadlines, timetable, exams and contests, always up to date.', new='NEW')
    await dr.click('[data-action="x-calendar-new"]', after=1.2)
    await dr.zoom('#calendar-link', 1.45, hold=2.6)
    await dr.zoom(None)

    await dr.nav('courses')
    await dr.click('.course-card .course-open')
    await dr.say('Study together', 'Share a course with a code. Friends get their own copy of its notes, tasks, exams and cards.', new='NEW')
    await dr.click('[data-action="share-course"]', after=1.0)
    await dr.click('[data-action="x-share-make"]', after=1.0)
    await dr.zoom('#modal .share-code', 1.6, hold=2.4)
    await dr.click('#modal [data-action="close"]')
    await dr.zoom(None)

    await dr.say('Four themes, including dark', 'Calm, Rose, Lavender and Dark. Arabic or English, on any device.', new='NEW')
    await dr.zoom('.theme-switch', 2.0)
    await dr.click('[data-theme-set="dark"]', after=1.4)
    await dr.zoom(None)
    await dr.nav('home', after=1.6)
    await dr.click('[data-theme-set="lavender"]', after=1.4)
    await dr.click('[data-theme-set="sage"]', after=1.0)
    await dr.say('')
    await dr.card(f'{LOGO}<h1>Happy studying!</h1><p>{SITE}</p><small>Free and open source · github.com/yousefJooX/studyhub</small>', 4.0)


async def script_fcsit(dr, base, data_dir):
    page = dr.page
    await dr.card(f'{LOGO}<h1>StudyHub</h1><p>For HNU FCSIT students: your level’s courses, tasks and archive.</p>', 3.6)
    await dr.card('', 0.7)
    await dr.say('Sign up as an FCSIT student', 'Students of the faculty get the archive, college tasks and announcements.')
    await signup(dr, 'Omar Khaled', 'omar', college=True)
    await dr.wait(1.0)
    await dr.say('Your overview', 'Today’s tasks, your timetable and the latest posts from your class channel.', hold=1.0)
    await dr.scroll('#board-news')
    await dr.say('College announcements', 'Straight from the WhatsApp channels of your level, through HNU Board.')
    await dr.zoom('#board-news', 1.35, hold=2.6)
    await dr.zoom(None)

    await dr.nav('archive', after=1.6)
    await dr.say('Step 1: add your courses', 'They come from the college archive of your level, with every lecture.', hold=1.2)
    await dr.zoom('.course-grid, .archive-list, #content .panel', 1.3, hold=1.4)
    await dr.zoom(None)
    await dr.click('[data-action="archive-files"]', after=1.4)
    await dr.say('Every lecture is one click away', 'PDFs and sheets uploaded for the course.')
    await dr.zoom('#modal .modal-body', 1.35, hold=2.0)
    await dr.click('#modal [data-action="close"]')
    await dr.zoom(None)
    await dr.say('Click Add to my courses', 'Do it for every course you take this term.')
    for course in ('c411', 'c401', 'c402'):
        await dr.click(f'[data-action="import-course"][data-source-course="{course}"]', after=0.9)
        await dr.click('#import-form button[type=submit]', after=1.2)

    await dr.say('Step 2: college tasks come by themselves', 'Every assignment for your level becomes a task, with its deadline and course.')
    await api(page, base, 'POST', '/api/board/sync', {})
    await reload_app(dr)
    await dr.nav('tasks', after=1.4)
    await dr.zoom('.task-row', 1.45, hold=2.4)
    await dr.say('You get reminded before every deadline', '1 day, 12 hours and 1 hour before. Missed ones show as closed.', hold=2.4)
    await dr.zoom(None)

    await dr.nav('exams')
    await dr.say('Plan your exams', 'Add the exam timetable and get a study plan for the days left.', new='NEW')
    for course, days, weight in (('ROB 411', 8, 5), ('ROB 401', 11, 3), ('ROB 402', 13, 4)):
        cid = await dr.js("t => S.courses.find(c => c.title.startsWith(t)).id", course)
        await api(page, base, 'POST', '/api/exams', {'course_id': cid, 'date': (date.today() + timedelta(days=days)).isoformat(), 'time': '09:00', 'weight': weight})
    await reload_app(dr)
    await dr.nav('exams', after=1.2)
    await dr.zoom('.exam-list', 1.3, hold=1.6)
    await dr.zoom(None)
    await dr.scroll('.plan-panel', 'start')
    await dr.click('[data-plan="preview"]', after=1.0)
    await dr.zoom('.plan-preview', 1.5, hold=2.2)
    await dr.click('[data-action="x-plan-make"]', after=1.2)
    await dr.zoom(None)

    await dr.say('Step 3: bring your Obsidian notes', 'Upload your notes folder or a ZIP, then link each course to its folder.')
    await api(page, base, 'POST', '/api/vault/upload-zip?name=My%20Notes.zip', raw=vault_zip())
    cv = await dr.js("() => S.courses.find(c => c.title.startsWith('ROB 411')).id")
    await api(page, base, 'PUT', f'/api/courses/{cv}', {'title': 'ROB 411 - Computer Vision', 'kind': 'college', 'vault_folder': 'My Notes/Computer Vision'})
    await api(page, base, 'POST', '/api/cards', {'cards': [{'front': 'Sobel filter detects?', 'back': 'Edges: the image gradient in x and y.', 'course_id': cv},
                                                           {'front': 'Harris corner response?', 'back': 'R = det(M) − k·trace(M)²', 'course_id': cv}]})
    await reload_app(dr)
    await dr.nav('courses', after=1.0)
    await dr.click('.course-card .course-open')
    await dr.say('The course page', 'Its tasks, college lectures and your notes in one place.', hold=2.4)

    await dr.say('Share it with your class', 'One code gives classmates their own copy of the notes, exams and flashcards.', new='NEW')
    await dr.click('[data-action="share-course"]', after=1.0)
    await dr.click('[data-action="x-share-make"]', after=1.0)
    await dr.zoom('#modal .share-code', 1.6, hold=2.4)
    await dr.click('#modal [data-action="close"]')
    await dr.zoom(None)

    await dr.nav('review', after=1.2)
    await dr.say('Flashcards for every course', 'Spaced repetition before the exam, made from your notes.', new='NEW', hold=1.0)
    await dr.click('[data-action="x-review-start"]', after=0.8)
    await dr.click('[data-action="x-flip"]', after=1.4)
    await dr.click('[data-x="grade"][data-ref="good"]', after=1.0)
    await dr.click('[data-action="x-review-end"]', after=0.6)

    await api(page, base, 'PUT', '/api/cp', {'codeforces': 'nour_codes', 'leetcode': 'nour_codes'})
    await dr.nav('cp', after=1.6)
    await dr.say('Competitive programming, built in', 'Codeforces and LeetCode stats, a daily problem and upcoming contests.', new='NEW', hold=1.0)
    await dr.zoom('.cp-grid', 1.3, hold=2.2)
    await dr.zoom(None)

    await dr.nav('settings', after=1.2)
    await dr.scroll('#calendar-link')
    await dr.say('Deadlines in your phone calendar', 'College tasks, lectures and exams in Google or iPhone Calendar.', new='NEW')
    await dr.click('[data-action="x-calendar-new"]', after=1.2)
    await dr.zoom('#calendar-link', 1.45, hold=2.2)
    await dr.zoom(None)
    await dr.say('The faculty archive bot on Telegram', 'One tap from the top bar.')
    await dr.zoom('.tg-link', 2.2, hold=2.0)
    await dr.zoom(None)
    await dr.say('')
    await dr.card(f'{LOGO}<h1>Happy studying!</h1><p>{SITE} · faculty code from your class group</p><small>Free and open source · github.com/yousefJooX/studyhub</small>', 4.0)


# ---------------------------------------------------------------- recording + encoding

def srt_time(seconds):
    ms = int(round(seconds * 1000))
    return f'{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}'


def write_subtitles(name, captions, title):
    srt, md = [], [f'# StudyHub walkthrough ({title}): transcript', '']
    for n, (start, end, text, sub) in enumerate(captions, 1):
        srt += [str(n), f'{srt_time(start)} --> {srt_time(end)}', text + ('\n' + sub if sub else ''), '']
        md.append(f'**{int(start) // 60:02d}:{int(start) % 60:02d}**  {text}. {sub}'.rstrip() + '  ')
    (DOCS / f'walkthrough-{name}.srt').write_text('\n'.join(srt), encoding='utf-8')
    (DOCS / f'walkthrough-transcript-{name}.md').write_text('\n'.join(md) + '\n', encoding='utf-8')


def encode(name, frames, duration, work):
    listing = work / 'frames.txt'
    with listing.open('w') as out:
        for (stamp, path), following in zip(frames, frames[1:] + [(frames[0][0] + duration, None)]):
            out.write(f"file '{path}'\nduration {max(0.001, following[0] - stamp):.4f}\n")
        out.write(f"file '{frames[-1][1]}'\n")
    target = DOCS / f'walkthrough-{name}.mp4'
    music = ['-stream_loop', '-1', '-i', str(MUSIC)] if MUSIC.is_file() else []
    audio = (['-map', '0:v', '-map', '1:a', '-af', f'afade=t=in:d=1.5,afade=t=out:st={max(0, duration - 3.5):.2f}:d=3.5',
              '-c:a', 'aac', '-b:a', '96k'] if music else [])
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', str(listing), *music,
                    '-vf', 'fps=30,format=yuv420p', '-c:v', 'libx264', '-preset', 'slow', '-crf', '24', '-tune', 'stillimage',
                    *audio, '-t', f'{duration:.2f}', '-movflags', '+faststart', str(target)], check=True)
    return target


async def record(name, title, script, total_hint):
    from playwright.async_api import async_playwright
    work = Path(tempfile.mkdtemp(prefix=f'studyhub-video-{name}-'))
    data, archive = work / 'data', work / 'archive'
    data.mkdir()
    make_archive(archive)
    board, cp, model = fake_board(), fake_cp(), fake_model()
    base, server = start_studyhub(data, board, cp, model, archive)
    frames_dir = work / 'frames'
    frames_dir.mkdir()
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=os.environ.get('STUDY_CHROME') or None)
        context = await browser.new_context(viewport={'width': 1920, 'height': 1080}, locale='en-GB', timezone_id='Africa/Cairo')
        await context.add_init_script("try{localStorage.setItem('studyhub-lang','en');localStorage.setItem('studyhub-theme','sage')}catch(e){}")
        page = await context.new_page()
        await page.goto(base + '/__stage')
        await page.evaluate('u => stage.url(u)', SITE)
        await page.frame(name='app').wait_for_selector('[data-auth-mode="signup"]', timeout=15000)
        await page.wait_for_timeout(600)
        director = Director(page, frames_dir, total_hint)
        await director.start()
        await script(director, base, data)
        duration = await director.stop()
        await browser.close()
    server.should_exit = True
    for fake in (board, cp, model):
        fake.shutdown()
    print(f'{name}: {len(director.frames)} frames, {duration:.1f}s')
    target = encode(name, director.frames, duration, work)
    write_subtitles(name, director.captions, title)
    shutil.rmtree(work, ignore_errors=True)
    print('wrote', target)


SCRIPTS = {'everyone': ('for everyone', script_everyone, 200), 'fcsit': ('for FCSIT students', script_fcsit, 170)}

if __name__ == '__main__':
    for key in sys.argv[1:] or list(SCRIPTS):
        title, script, hint = SCRIPTS[key]
        asyncio.run(record(key, title, script, hint))
