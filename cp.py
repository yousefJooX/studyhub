"""Competitive programming tracker: Codeforces and LeetCode.

The student adds their Codeforces handle and/or LeetCode username. StudyHub reads the public APIs
(nothing is sent but the handle) and shows rating, solved problems, the daily streak, recent solves,
a daily problem and the upcoming contests. Contests can go into the calendar feed and the tasks.

Stats are cached per user for STATS_TTL, the contest list and the Codeforces problem set are cached
for everyone. A failed request never breaks the page: the last good data is shown with an error flag.
Config: STUDY_CF_API, STUDY_LC_API (only changed by the tests).
"""
import hashlib
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from . import accounts
from . import app as core

router = APIRouter(prefix='/api/cp', dependencies=[Depends(core.authorized)])
CAIRO = ZoneInfo('Africa/Cairo')
STATS_TTL = 30 * 60
CONTESTS_TTL = 60 * 60
HANDLE = re.compile(r'^[A-Za-z0-9_.-]{1,40}$')
_lock = threading.Lock()
NETWORK = (urllib.error.URLError, TimeoutError, ValueError, KeyError, OSError)

LC_STATS = '''query($u: String!) {
  matchedUser(username: $u) { submitStatsGlobal { acSubmissionNum { difficulty count } }
    userCalendar { streak totalActiveDays } }
  userContestRanking(username: $u) { rating attendedContestsCount }
  recentAcSubmissionList(username: $u, limit: 5) { title titleSlug timestamp }
}'''
LC_PUBLIC = '''query { activeDailyCodingChallengeQuestion { date link question { title difficulty } }
  upcomingContests { title titleSlug startTime duration } }'''


def cf_api():
    return os.environ.get('STUDY_CF_API', 'https://codeforces.com/api').rstrip('/')


def lc_api():
    return os.environ.get('STUDY_LC_API', 'https://leetcode.com/graphql')


def _request(url, data=None, timeout=20, limit=30_000_000):
    headers = {'User-Agent': 'StudyHub/1.0 (+https://github.com/yousefJooX/studyhub)'}
    if data is not None:
        headers |= {'Content-Type': 'application/json', 'Referer': 'https://leetcode.com'}
        data = json.dumps(data).encode()
    request = urllib.request.Request(url, data=data, headers=headers, method='POST' if data else 'GET')
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read(limit))


def cf(method, **params):
    query = '&'.join(f'{k}={urllib.parse.quote(str(v))}' for k, v in params.items())
    try:
        body = _request(f'{cf_api()}/{method}' + (f'?{query}' if query else ''))
    except urllib.error.HTTPError as error:  # Codeforces answers 400 with a JSON comment for unknown handles
        body = json.loads(error.read() or b'{}')
    if body.get('status') != 'OK':
        raise LookupError(body.get('comment') or 'Codeforces error')
    return body['result']


def lc(query, **variables):
    body = _request(lc_api(), {'query': query, 'variables': variables})
    if body.get('errors') and not body.get('data'):
        raise LookupError(body['errors'][0].get('message', 'LeetCode error'))
    return body.get('data') or {}


def cairo_day(seconds):
    return datetime.fromtimestamp(seconds, CAIRO).date()


def streak(days):
    """Days in a row with a solve, ending today or yesterday (today is not over yet)."""
    today = datetime.now(CAIRO).date()
    day = today if today in days else today - timedelta(days=1)
    count = 0
    while day in days:
        count += 1
        day -= timedelta(days=1)
    return count


def codeforces_stats(handle):
    info = cf('user.info', handles=handle)[0]
    submissions = cf('user.status', handle=handle, **{'from': 1, 'count': 2000})
    solved, days, recent, week_ago = {}, set(), [], time.time() - 7 * 86400
    for sub in sorted(submissions, key=lambda s: s['creationTimeSeconds']):
        if sub.get('verdict') != 'OK':
            continue
        p = sub['problem']
        key = f"{p.get('contestId', '')}{p.get('index', '')}"
        days.add(cairo_day(sub['creationTimeSeconds']))
        if key not in solved:
            solved[key] = {'name': p.get('name', key), 'rating': p.get('rating'), 'tags': p.get('tags', []),
                           'at': sub['creationTimeSeconds'],
                           'url': f"https://codeforces.com/problemset/problem/{p.get('contestId')}/{p.get('index')}"}
    for item in sorted(solved.values(), key=lambda s: s['at'], reverse=True)[:5]:
        recent.append({'name': item['name'], 'rating': item['rating'], 'url': item['url'], 'at': item['at']})
    tags = {}
    for item in solved.values():
        for tag in item['tags']:
            tags[tag] = tags.get(tag, 0) + 1
    return {'handle': info['handle'], 'rating': info.get('rating'), 'max_rating': info.get('maxRating'),
            'rank': info.get('rank', 'unrated'), 'max_rank': info.get('maxRank', ''), 'solved': len(solved),
            'solved_week': sum(1 for s in solved.values() if s['at'] >= week_ago), 'streak': streak(days),
            'recent': recent, 'top_tags': sorted(tags.items(), key=lambda t: -t[1])[:6],
            'solved_keys': sorted(solved), 'url': f"https://codeforces.com/profile/{info['handle']}"}


def leetcode_stats(username):
    data = lc(LC_STATS, u=username)
    user = data.get('matchedUser')
    if not user:
        raise LookupError('user not found')
    counts = {c['difficulty'].lower(): c['count'] for c in user['submitStatsGlobal']['acSubmissionNum']}
    ranking = data.get('userContestRanking') or {}
    calendar = user.get('userCalendar') or {}
    return {'username': username, 'solved': counts.get('all', 0), 'easy': counts.get('easy', 0), 'medium': counts.get('medium', 0),
            'hard': counts.get('hard', 0), 'rating': round(ranking['rating']) if ranking.get('rating') else None,
            'contests': ranking.get('attendedContestsCount') or 0, 'streak': calendar.get('streak') or 0,
            'active_days': calendar.get('totalActiveDays') or 0,
            'recent': [{'name': r['title'], 'url': f"https://leetcode.com/problems/{r['titleSlug']}/", 'at': int(r['timestamp'])}
                       for r in (data.get('recentAcSubmissionList') or [])],
            'url': f'https://leetcode.com/u/{username}/'}


# ---------------------------------------------------------------- shared caches (all users)

def _shared(name, ttl, build):
    path = accounts.base_root() / f'cp-{name}.json'
    with _lock:
        try:
            cached = json.loads(path.read_text())
        except (OSError, ValueError):
            cached = None
        if cached and time.time() - cached['fetched'] < ttl:
            return cached['data']
        try:
            data = build()
        except (NETWORK + (LookupError,)) as error:
            logging.warning('cp %s: %s', name, type(error).__name__)
            return cached['data'] if cached else None
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps({'fetched': time.time(), 'data': data}, ensure_ascii=False))
        tmp.replace(path)
        return data


def _contests():
    found = []
    try:
        for c in cf('contest.list', gym='false'):
            if c.get('phase') == 'BEFORE' and c.get('startTimeSeconds'):
                found.append({'id': f"cf-{c['id']}", 'platform': 'Codeforces', 'name': c['name'],
                              'start': datetime.fromtimestamp(c['startTimeSeconds'], timezone.utc).isoformat(),
                              'duration_minutes': c.get('durationSeconds', 7200) // 60, 'url': f"https://codeforces.com/contests/{c['id']}"})
    except (NETWORK + (LookupError,)):
        pass
    daily = None
    try:
        data = lc(LC_PUBLIC)
        for c in data.get('upcomingContests') or []:
            found.append({'id': f"lc-{c['titleSlug']}", 'platform': 'LeetCode', 'name': c['title'],
                          'start': datetime.fromtimestamp(c['startTime'], timezone.utc).isoformat(),
                          'duration_minutes': c.get('duration', 5400) // 60, 'url': f"https://leetcode.com/contest/{c['titleSlug']}/"})
        q = data.get('activeDailyCodingChallengeQuestion')
        if q:
            daily = {'title': q['question']['title'], 'difficulty': q['question']['difficulty'], 'date': q['date'],
                     'url': 'https://leetcode.com' + q['link']}
    except (NETWORK + (LookupError,)):
        pass
    if not found and not daily:
        raise LookupError('no contest source answered')
    return {'contests': sorted(found, key=lambda c: c['start']), 'leetcode_daily': daily}


def public_data():
    data = _shared('contests', CONTESTS_TTL, _contests) or {'contests': [], 'leetcode_daily': None}
    now = datetime.now(timezone.utc)
    soon = [c for c in data['contests'] if now - timedelta(hours=3) < datetime.fromisoformat(c['start']) < now + timedelta(days=21)]
    return {'contests': soon[:15], 'leetcode_daily': data.get('leetcode_daily')}


def _problemset():
    return [{'id': f"{p['contestId']}{p['index']}", 'name': p['name'], 'rating': p['rating'],
             'url': f"https://codeforces.com/problemset/problem/{p['contestId']}/{p['index']}"}
            for p in cf('problemset.problems')['problems'] if p.get('rating') and p.get('contestId')]


def codeforces_daily(stats):
    """One unsolved problem a little above the student's rating, the same all day."""
    problems = _shared('problemset', 24 * 3600, _problemset)
    if not problems:
        return None
    target = min(3500, max(800, round(((stats.get('rating') or 700) + 100) / 100) * 100))
    solved = set(stats.get('solved_keys', []))
    pool = [p for p in problems if p['rating'] == target and p['id'] not in solved] or [p for p in problems if p['id'] not in solved]
    if not pool:
        return None
    seed = hashlib.sha256(f"{datetime.now(CAIRO).date()}:{stats['handle']}".encode()).digest()
    return pool[int.from_bytes(seed[:4], 'big') % len(pool)]


# ---------------------------------------------------------------- per-user settings and stats

class Handles(BaseModel):
    codeforces: str = Field(default='', max_length=40)
    leetcode: str = Field(default='', max_length=40)
    contests_in_calendar: bool = True

    @field_validator('codeforces', 'leetcode')
    @classmethod
    def valid_handle(cls, value):
        value = value.strip().lstrip('@')
        if value and not HANDLE.fullmatch(value):
            raise ValueError('Handle: letters, digits, _ . - only')
        return value


def handles():
    return core.setting('cp', {'codeforces': '', 'leetcode': '', 'contests_in_calendar': True})


def user_stats(force=False):
    saved = handles()
    cache = core.setting('cp_cache', {})
    fresh = cache.get('key') == [saved['codeforces'], saved['leetcode']] and time.time() - cache.get('fetched', 0) < STATS_TTL
    if fresh and not force:
        return cache
    result = {'key': [saved['codeforces'], saved['leetcode']], 'fetched': time.time(), 'errors': {}}
    for name, handle, fetch in (('codeforces', saved['codeforces'], codeforces_stats), ('leetcode', saved['leetcode'], leetcode_stats)):
        if not handle:
            continue
        try:
            result[name] = fetch(handle)
        except LookupError:
            result['errors'][name] = 'not_found'
        except NETWORK as error:
            result['errors'][name] = 'network'
            if cache.get(name) and cache.get('key') == result['key']:
                result[name] = cache[name]  # keep showing the last good numbers
            logging.warning('cp %s: %s', name, type(error).__name__)
    core.set_setting('cp_cache', result)
    return result


@router.get('')
def overview(refresh: bool = False):
    saved = handles()
    if refresh:
        user = accounts.CURRENT_USER.get()
        accounts.throttle('cp:' + (user['id'] if user else 'owner'), 30, 3600)
    stats = user_stats(force=refresh) if saved['codeforces'] or saved['leetcode'] else {'errors': {}}
    public = public_data()
    cf_stats = stats.get('codeforces')
    return {'handles': saved, 'codeforces': {k: v for k, v in cf_stats.items() if k != 'solved_keys'} if cf_stats else None,
            'leetcode': stats.get('leetcode'), 'errors': stats.get('errors', {}), 'fetched': stats.get('fetched'),
            'daily': {'codeforces': codeforces_daily(cf_stats) if cf_stats else None, 'leetcode': public['leetcode_daily']},
            'contests': public['contests']}


@router.put('')
def save_handles(body: Handles):
    user = accounts.CURRENT_USER.get()
    accounts.throttle('cp-save:' + (user['id'] if user else 'owner'), 30, 3600)
    before = handles()
    core.set_setting('cp', body.model_dump())
    stats = user_stats(force=True)
    missing = [name for name in ('codeforces', 'leetcode') if stats['errors'].get(name) == 'not_found']
    if missing:
        core.set_setting('cp', before)
        core.set_setting('cp_cache', {})
        names = ' و'.join({'codeforces': 'Codeforces', 'leetcode': 'LeetCode'}[m] for m in missing)
        raise HTTPException(400, f'مش لاقي الحساب ده على {names}. اتأكد من الاسم')
    return overview()


def calendar_contests():
    """Upcoming contests for the calendar feed, only when the user follows CP and kept the option on."""
    saved = handles()
    if not (saved.get('codeforces') or saved.get('leetcode')) or not saved.get('contests_in_calendar', True):
        return []
    wanted = {p for p, h in (('Codeforces', saved.get('codeforces')), ('LeetCode', saved.get('leetcode'))) if h}
    return [c for c in public_data()['contests'] if c['platform'] in wanted]
