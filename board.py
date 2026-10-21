"""College tasks from HNU Board (https://github.com/moustafasayed068/hnu-board, by Moustafa Sayed).

HNU Board collects every assignment and announcement posted on the faculty's WhatsApp channels
and sorts them by level and program. StudyHub reads its public board pages (read-only, nothing
is sent to it) and adds each new assignment for the student's level and program to their tasks,
with the deadline, the course and a link back to the post.

Config: STUDY_BOARD_URL (default the public HNU Board site), STUDY_BOARD_SYNC=0 to turn it off.
"""
import html
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from . import accounts
from . import app as core

router = APIRouter(prefix='/api/board', dependencies=[Depends(core.authorized)])
DEFAULT_URL = 'https://mosuatafa.pythonanywhere.com'
REFRESH = 15 * 60          # how often a board page is fetched again
USER_EVERY = 10 * 60       # how often one user's tasks are synced
_lock = threading.Lock()
CARD = re.compile(r'<article class="card card--(assignment|announcement)[^"]*">(.*?)</article>', re.S)


def base_url():
    return os.environ.get('STUDY_BOARD_URL', DEFAULT_URL).rstrip('/')


def enabled():
    return os.environ.get('STUDY_BOARD_SYNC', '1') != '0'


def _get(url, timeout=15):
    request = urllib.request.Request(url, headers={'User-Agent': 'StudyHub/1.0 (+https://github.com/yousefJooX/studyhub)'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(2_000_000).decode('utf-8', errors='replace')


def _text(fragment):
    return html.unescape(re.sub(r'<[^>]+>', '', fragment or '')).strip()


def parse_board(page):
    """Cards on a board page → [{id, kind, title, course_code, course_name, body}]."""
    posts, seen = [], set()
    for kind, body in CARD.findall(page):
        link = re.search(r'href="/post/(\d+)/"', body)
        if not link or link.group(1) in seen:
            continue
        seen.add(link.group(1))
        title = re.search(r'<h3[^>]*>(.*?)</h3>', body, re.S)
        code = re.search(r'<span class="code">(.*?)</span>', body, re.S)
        course = re.search(r'<p class="card__course">(.*?)</p>', body, re.S)
        text = re.search(r'<p class="card__body"[^>]*>(.*?)</p>', body, re.S)
        posts.append({'id': link.group(1), 'kind': kind, 'title': _text(title.group(1) if title else '')[:280],
                      'course_code': _text(code.group(1) if code else ''), 'course_name': _text(course.group(1) if course else ''),
                      'body': _text(text.group(1) if text else '')[:2000]})
    return posts


def parse_deadline(ics):
    match = re.search(r'^DTSTART:(\d{8}T\d{6})Z', ics, re.M)
    if not match:
        return ''
    return datetime.strptime(match.group(1), '%Y%m%dT%H%M%S').replace(tzinfo=timezone.utc).isoformat()


def _cache_path():
    return accounts.base_root() / 'board-cache.json'


def _load_cache():
    try:
        return json.loads(_cache_path().read_text())
    except (OSError, ValueError):
        return {'boards': {}, 'deadlines': {}}


def board_posts(level, program):
    """Assignments + announcements for one level/program, cached and shared by all users."""
    key = f'{level}:{program}'
    with _lock:
        cache = _load_cache()
        entry = cache['boards'].get(key)
        if entry and time.time() - entry['fetched'] < REFRESH:
            return entry['posts']
        page = _get(f'{base_url()}/?year={int(level)}&program={program}')
        posts = parse_board(page)
        for post in posts:
            if post['kind'] != 'assignment':
                continue
            if post['id'] not in cache['deadlines']:
                try:
                    cache['deadlines'][post['id']] = parse_deadline(_get(f"{base_url()}/post/{post['id']}/calendar.ics"))
                except (urllib.error.URLError, TimeoutError, ValueError):
                    continue
            post['deadline'] = cache['deadlines'].get(post['id'], '')
        cache['boards'][key] = {'fetched': time.time(), 'posts': posts}
        tmp = _cache_path().with_suffix('.tmp')
        tmp.write_text(json.dumps(cache, ensure_ascii=False))
        tmp.replace(_cache_path())
        return posts


def posts_for(level, program):
    if program:
        return board_posts(level, program)
    # No program chosen yet: only posts every program of this level sees (whole-level posts).
    boards = [board_posts(level, p) for p in accounts.PROGRAMS]
    common = set.intersection(*({p['id'] for p in b} for b in boards))
    return [p for p in boards[0] if p['id'] in common]


def _course_for(post, courses):
    code, name = post.get('course_code', '').lower(), post.get('course_name', '').lower()
    for course in courses:
        title = course['title'].lower()
        if (code and code in title) or (name and (name in title or title in name)):
            return course['id']
    if not (code or name):
        return ''
    import uuid
    created = core.put('courses', {'id': uuid.uuid4().hex, 'title': (post['course_name'] or post['course_code'])[:200],
                                   'kind': 'college', 'vault_folder': '', 'created': time.time(), 'board_code': post['course_code']})
    courses.append(created)
    return created['id']


def sync(force=False):
    """Add new assignments from the board to the current user's tasks. Returns how many were added."""
    profile = accounts.college_profile()
    settings = core.setting('board_sync', {'enabled': True, 'imported': [], 'last': 0})
    if not enabled() or not profile.get('college') or not profile.get('level') or not settings.get('enabled', True):
        return {'added': 0, 'skipped': 'off'}
    if not force and time.time() - settings.get('last', 0) < USER_EVERY:
        return {'added': 0, 'skipped': 'recent'}
    posts = posts_for(profile['level'], profile.get('program', ''))
    imported, added = set(settings.get('imported', [])), []
    courses = core.rows('courses')
    now = datetime.now(timezone.utc)
    for post in posts:
        if post['kind'] != 'assignment' or post['id'] in imported or not post.get('deadline'):
            continue
        if datetime.fromisoformat(post['deadline']) < now:
            imported.add(post['id'])  # already closed: don't add it later either
            continue
        link = f"{base_url()}/post/{post['id']}/"
        details = (post['body'] + '\n\n' if post['body'] else '') + 'من قناة الكلية عبر HNU Board: ' + link
        task = core.Task(title=post['title'] or 'Assignment', course_id=_course_for(post, courses), due=post['deadline'],
                         details=details[:10000], priority='high')
        core.save_entry('tasks', task)
        imported.add(post['id'])
        added.append(post['title'])
    core.set_setting('board_sync', settings | {'imported': sorted(imported)[-2000:], 'last': time.time(), 'error': ''})
    return {'added': len(added), 'titles': added}


def sync_quietly():
    """Called by the background worker; never raises."""
    try:
        sync()
    except Exception as error:  # network down, board changed…: keep the worker alive
        logging.warning('HNU Board sync: %s', type(error).__name__)
        try:
            settings = core.setting('board_sync', {'enabled': True, 'imported': [], 'last': 0})
            core.set_setting('board_sync', settings | {'last': time.time(), 'error': type(error).__name__})
        except Exception:
            pass


@router.get('')
def board_status():
    settings = core.setting('board_sync', {'enabled': True, 'imported': [], 'last': 0})
    return {'available': enabled(), 'enabled': settings.get('enabled', True), 'last': settings.get('last', 0),
            'imported': len(settings.get('imported', [])), 'error': settings.get('error', ''),
            'url': base_url(), 'profile': accounts.college_profile()}


@router.post('/sync')
def board_sync_now():
    profile = accounts.college_profile()
    if not profile.get('college') or not profile.get('level'):
        raise HTTPException(400, 'اختار سنتك في الكلية الأول')
    user = accounts.CURRENT_USER.get()
    accounts.throttle('board:' + (user['id'] if user else 'owner'), 20, 3600)
    try:
        return sync(force=True)
    except (urllib.error.URLError, TimeoutError) as error:
        raise HTTPException(502, f'مش قادر أوصل لـ HNU Board دلوقتي ({type(error).__name__})')


@router.put('')
def board_toggle(body: dict):
    settings = core.setting('board_sync', {'enabled': True, 'imported': [], 'last': 0})
    core.set_setting('board_sync', settings | {'enabled': bool(body.get('enabled'))})
    return {'enabled': bool(body.get('enabled'))}
