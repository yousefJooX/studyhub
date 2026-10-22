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
        posted = re.search(r'Posted (\d{1,2} \w{3} \d{4})', body)
        posts.append({'id': link.group(1), 'kind': kind, 'title': _text(title.group(1) if title else '')[:280],
                      'course_code': _text(code.group(1) if code else ''), 'course_name': _text(course.group(1) if course else ''),
                      'body': _text(text.group(1) if text else '')[:2000],
                      'posted': _posted(posted.group(1)) if posted else ''})
    return posts


def _posted(value):
    try:
        return datetime.strptime(value, '%d %b %Y').date().isoformat()
    except ValueError:
        return ''


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


def parse_courses(page):
    """Course picker on a board page → [{'code', 'name'}] (only the courses of that level/program)."""
    select = re.search(r'<select name="course"[^>]*>(.*?)</select>', page, re.S)
    found = []
    for label in re.findall(r'<option value="\d+"[^>]*>(.*?)</option>', select.group(1) if select else '', re.S):
        code, _, name = _text(label).partition(' - ')
        if name:
            found.append({'code': code.strip(), 'name': name.strip()})
    return found


def board_posts(level, program, with_courses=False):
    """Posts (announcements + every assignment, open or closed) for one level/program,
    cached for REFRESH seconds and shared by all users."""
    key = f'{level}:{program}'
    with _lock:
        cache = _load_cache()
        entry = cache['boards'].get(key)
        if not (entry and time.time() - entry['fetched'] < REFRESH and 'courses' in entry):
            base = f'{base_url()}/?year={int(level)}&program={program}'
            page = _get(base)
            posts, ids = parse_board(page), set()
            ids = {p['id'] for p in posts}
            for number in range(1, 6):  # older assignments live on later pages
                more = [p for p in parse_board(_get(f'{base}&type=assignment&page={number}')) if p['id'] not in ids]
                if not more:
                    break
                posts += more
                ids |= {p['id'] for p in more}
            for post in posts:
                if post['kind'] != 'assignment':
                    continue
                if post['id'] not in cache['deadlines']:
                    try:
                        cache['deadlines'][post['id']] = parse_deadline(_get(f"{base_url()}/post/{post['id']}/calendar.ics"))
                    except (urllib.error.URLError, TimeoutError, ValueError):
                        continue
                post['deadline'] = cache['deadlines'].get(post['id'], '')
            entry = {'fetched': time.time(), 'posts': posts, 'courses': parse_courses(page)}
            cache['boards'][key] = entry
            tmp = _cache_path().with_suffix('.tmp')
            tmp.write_text(json.dumps(cache, ensure_ascii=False))
            tmp.replace(_cache_path())
        return (entry['posts'], entry['courses']) if with_courses else entry['posts']


def posts_for(level, program, with_courses=False):
    if program:
        return board_posts(level, program, with_courses)
    # No program chosen yet: only what every program of this level sees (whole-level posts, common courses).
    boards = [board_posts(level, p, True) for p in accounts.PROGRAMS]
    common = set.intersection(*({p['id'] for p in posts} for posts, _ in boards))
    posts = [p for p in boards[0][0] if p['id'] in common]
    codes = set.intersection(*({c['code'] for c in courses} for _, courses in boards))
    courses = [c for c in boards[0][1] if c['code'] in codes]
    return (posts, courses) if with_courses else posts


def _course_for(post, courses):
    """Link a post to one of the user's OWN courses (by board code or exact name). Never creates a course:
    the board's course list mixes terms and tracks, so the user's courses come from the college archive."""
    code, name = post.get('course_code', '').strip().lower(), post.get('course_name', '').strip().lower()
    for course in courses:
        title = course['title'].strip().lower()
        if (code and (course.get('board_code', '').strip().lower() == code or re.search(rf'(^|\W){re.escape(code)}(\W|$)', title))) \
                or (name and title == name):
            return course['id']
    return ''


def _course_label(post):
    code, name = post.get('course_code', '').strip(), post.get('course_name', '').strip()
    return f'{code} · {name}' if code and name else (name or code)


def _drop_auto_courses():
    """Older versions created a course for every entry in the board's course list (wrong term / other tracks).
    Remove those again, but only when the user never used them: no slots, notes, files, own tasks or vault folder."""
    courses = [c for c in core.rows('courses') if c.get('board_code') and not c.get('archive_course') and not c.get('vault_folder')]
    if not courses:
        return 0
    used = set()
    for table in ('slots', 'notes', 'uploads'):
        used |= {r.get('course_id') for r in core.rows(table)}
    used |= {t.get('course_id') for t in core.rows('tasks') if not t.get('board')}
    removed = 0
    for course in courses:
        if course['id'] in used:
            continue
        for task in core.rows('tasks'):
            if task.get('course_id') == course['id'] and task.get('board'):
                core.put('tasks', task | {'course_id': '', 'board': task['board'] | {'course': course['title']}})
        core.remove_course(course['id'])
        removed += 1
    return removed


def sync(force=False):
    """Bring the board into the user's space: every assignment of their level/program becomes a task, linked to
    one of their own courses when it matches, otherwise labelled with the board's course name. Open ones stay open; ones whose deadline already passed come in as closed
    (marked done, labelled "closed" and left out of the weekly review)."""
    profile = accounts.college_profile()
    settings = core.setting('board_sync', {'enabled': True, 'last': 0})
    if not enabled() or not profile.get('college') or not profile.get('level') or not settings.get('enabled', True):
        return {'added': 0, 'skipped': 'off'}
    if not force and time.time() - settings.get('last', 0) < USER_EVERY:
        return {'added': 0, 'skipped': 'recent'}
    level, program = profile['level'], profile.get('program', '')
    posts, board_courses = posts_for(level, program, with_courses=True)
    courses = core.rows('courses')
    if not settings.get('courses_cleaned'):
        _drop_auto_courses()
        settings['courses_cleaned'] = True
        courses = core.rows('courses')
    tasks = core.rows('tasks')
    seen = set(settings.get('created', []))
    for t in tasks:
        board = t.get('board') or {}
        if board.get('id'):
            seen.add(str(board['id']))
            continue
        old = re.search(r'/post/(\d+)/', t.get('details', ''))
        if old and 'HNU Board' in t.get('details', ''):  # imported before tasks carried a board tag
            seen.add(old.group(1))
            core.put('tasks', t | {'board': {'id': old.group(1), 'url': f"{base_url()}/post/{old.group(1)}/", 'closed': False}})
    created, added, closed = set(settings.get('created', [])), [], 0
    now = datetime.now(timezone.utc)
    for post in posts:
        if post['kind'] != 'assignment' or post['id'] in seen or not post.get('deadline'):
            continue
        is_closed = datetime.fromisoformat(post['deadline']) < now
        link = f"{base_url()}/post/{post['id']}/"
        details = (post['body'] + '\n\n' if post['body'] else '') + 'من قناة الكلية عبر HNU Board: ' + link
        task = core.Task(title=post['title'] or 'Assignment', course_id=_course_for(post, courses), due=post['deadline'],
                         details=details[:10000], priority='normal' if is_closed else 'high', status='done' if is_closed else 'todo')
        value = core.save_entry('tasks', task)
        core.put('tasks', value | {'board': {'id': post['id'], 'url': link, 'closed': is_closed, 'course': _course_label(post)}})
        created.add(post['id'])
        if is_closed:
            closed += 1
        else:
            added.append(post['title'])
    core.set_setting('board_sync', settings | {'created': sorted(created)[-3000:], 'last': time.time(), 'error': ''})
    return {'added': len(added), 'closed': closed, 'titles': added}


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
    settings = core.setting('board_sync', {'enabled': True, 'last': 0})
    return {'available': enabled(), 'enabled': settings.get('enabled', True), 'last': settings.get('last', 0),
            'imported': len(settings.get('created', [])), 'error': settings.get('error', ''),
            'url': base_url(), 'profile': accounts.college_profile()}


@router.get('/announcements')
def announcements(limit: int = 8):
    """Latest faculty announcements for the user's level/program (shown on the overview)."""
    profile = accounts.college_profile()
    if not enabled() or not profile.get('college') or not profile.get('level'):
        return {'items': [], 'available': False}
    try:
        posts = posts_for(profile['level'], profile.get('program', ''))
    except (urllib.error.URLError, TimeoutError, ValueError):
        return {'items': [], 'available': True, 'error': True, 'url': base_url()}
    items = [p | {'url': f"{base_url()}/post/{p['id']}/"} for p in posts if p['kind'] == 'announcement']
    items.sort(key=lambda p: (p.get('posted', ''), int(p['id'])), reverse=True)
    level, program = profile['level'], profile.get('program') or 'ds'
    return {'items': items[:max(1, min(limit, 20))], 'available': True,
            'board': f'{base_url()}/?year={int(level)}&program={program}'}


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
