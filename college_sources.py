"""Where a faculty's assignments and announcements come from: one adapter per source.

StudyHub turns a faculty's posts into college tasks (with deadlines) and overview announcements for
each student's level and program. The sync logic lives in board.py and is the same for every faculty;
an adapter only answers "what are the posts for this level/program?".

Built-in adapters (pick one with STUDY_COLLEGE_SOURCE):
  hnu_board  (default)  HNU Board for HNU FCSIT, see board.py.
  json_feed             Any faculty: publish a JSON file per level/program and set STUDY_COLLEGE_FEED_URL,
                        e.g. https://example.edu/feed/{level}/{program}.json  ({program} may be empty).
  none                  College tasks and announcements off; the archive and the code still work.

The json_feed format (also what a new adapter must return from posts()):
  {"posts": [{"id": "123", "kind": "assignment" | "announcement", "title": "...",
              "course_code": "CS201", "course_name": "Data Structures", "body": "...",
              "posted": "2026-10-01", "deadline": "2026-10-20T21:59:00+00:00", "url": "https://..."}],
   "courses": [{"code": "CS201", "name": "Data Structures"}]}
A deadline must carry a time zone; posts without one are shown but never become tasks.

Adding an adapter: write a class with name, posts(level, program) -> (posts, courses), home(level, program)
and register it in ADAPTERS. tests/test_units.py shows how the json_feed one is tested.

The faculty's levels and programs are configured too:
  STUDY_COLLEGE_LEVELS    number of years (default 4)
  STUDY_COLLEGE_PROGRAMS  "ds=Data Science (DS);mse=Multimedia Software (MSE)" (default: HNU FCSIT's three)
"""
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request

from . import accounts

FEED_TTL = 15 * 60
_lock = threading.Lock()
HNU_PROGRAMS = {'ds': 'Data Science (DS)', 'mse': 'Multimedia Software (MSE)', 'rse': 'Robotics Software (RSE)'}


def programs():
    raw = os.environ.get('STUDY_COLLEGE_PROGRAMS', '').strip()
    if not raw:
        return dict(HNU_PROGRAMS)
    found = {}
    for part in raw.split(';'):
        key, _, name = part.partition('=')
        key = key.strip().lower()
        if re.fullmatch(r'[a-z0-9_-]{1,20}', key):
            found[key] = name.strip() or key.upper()
    return found or dict(HNU_PROGRAMS)


def levels():
    try:
        return max(1, min(7, int(os.environ.get('STUDY_COLLEGE_LEVELS', '4'))))
    except ValueError:
        return 4


def clean_post(post):
    kind = post.get('kind') if post.get('kind') in ('assignment', 'announcement') else 'announcement'
    url = str(post.get('url') or '')
    return {'id': str(post.get('id', ''))[:64], 'kind': kind, 'title': str(post.get('title') or '')[:280],
            'course_code': str(post.get('course_code') or '')[:40], 'course_name': str(post.get('course_name') or '')[:200],
            'body': str(post.get('body') or '')[:2000], 'posted': str(post.get('posted') or '')[:10],
            'deadline': str(post.get('deadline') or ''), 'url': url if url.startswith(('https://', 'http://')) else ''}


class HNUBoard:
    name = 'HNU Board'

    def posts(self, level, program):
        from . import board
        posts, courses = board.posts_for(level, program, with_courses=True)
        return [p | {'url': p.get('url') or f"{board.base_url()}/post/{p['id']}/"} for p in posts], courses

    def home(self, level, program):
        from . import board
        return f'{board.base_url()}/?year={int(level)}&program={program or next(iter(programs()))}'


class JSONFeed:
    @property
    def name(self):
        return os.environ.get('STUDY_COLLEGE_SOURCE_NAME', 'قناة الكلية')

    def url(self, level, program):
        template = os.environ.get('STUDY_COLLEGE_FEED_URL', '')
        if not template:
            raise ValueError('STUDY_COLLEGE_FEED_URL is not set')
        return template.replace('{level}', str(int(level))).replace('{program}', urllib.parse.quote(program or ''))

    def posts(self, level, program):
        url = self.url(level, program)
        path = accounts.base_root() / 'college-feed-cache.json'
        with _lock:
            try:
                cache = json.loads(path.read_text())
            except (OSError, ValueError):
                cache = {}
            entry = cache.get(url)
            if not entry or time.time() - entry['fetched'] > FEED_TTL:
                request = urllib.request.Request(url, headers={'User-Agent': 'StudyHub/1.0 (+https://github.com/yousefJooX/studyhub)'})
                with urllib.request.urlopen(request, timeout=15) as response:
                    data = json.loads(response.read(5_000_000))
                posts = [clean_post(p) for p in data.get('posts', []) if isinstance(p, dict) and p.get('id')]
                courses = [{'code': str(c.get('code', ''))[:40], 'name': str(c.get('name', ''))[:200]}
                           for c in data.get('courses', []) if isinstance(c, dict)]
                entry = {'fetched': time.time(), 'posts': posts, 'courses': courses}
                cache[url] = entry
                tmp = path.with_suffix('.tmp')
                tmp.write_text(json.dumps(cache, ensure_ascii=False))
                tmp.replace(path)
        return entry['posts'], entry['courses']

    def home(self, level, program):
        return os.environ.get('STUDY_COLLEGE_HOME_URL', '')


class NoSource:
    name = ''

    def posts(self, level, program):
        return [], []

    def home(self, level, program):
        return ''


ADAPTERS = {'hnu_board': HNUBoard, 'json_feed': JSONFeed, 'none': NoSource}


def source():
    return ADAPTERS.get(os.environ.get('STUDY_COLLEGE_SOURCE', 'hnu_board'), HNUBoard)()


def enabled():
    return os.environ.get('STUDY_COLLEGE_SOURCE', 'hnu_board') != 'none'
