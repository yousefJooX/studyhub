"""A private calendar feed (iCalendar) for Google Calendar, Apple Calendar and Outlook.

The user creates a secret link once in the settings and subscribes to it ("From URL" in Google Calendar).
The feed carries open deadlines, tasks planned at a time, the weekly timetable (repeating), exams and,
when switched on in the competitive-programming page, upcoming contests. It is read-only: the link
can only read the calendar, so it is separate from the API/MCP token, and only its SHA-256 is stored.
"""
import hashlib
import hmac
import os
import secrets
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from . import accounts
from . import app as core

router = APIRouter()
CAIRO = ZoneInfo('Africa/Cairo')
KIND = {'ar': {'study': 'مذاكرة', 'lecture': 'محاضرة', 'section': 'سكشن', 'lab': 'لاب'},
        'en': {'study': 'Study', 'lecture': 'Lecture', 'section': 'Section', 'lab': 'Lab'}}


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def tokens_table(db):
    db.execute('CREATE TABLE IF NOT EXISTS calendar_tokens(token TEXT PRIMARY KEY, user_id TEXT UNIQUE NOT NULL, created REAL NOT NULL)')


def public_origin(request):
    return os.environ.get('STUDY_PUBLIC_ORIGIN', '').rstrip('/') or str(request.base_url).rstrip('/')


@router.get('/api/calendar', dependencies=[Depends(core.authorized)])
def calendar_status():
    if accounts.enabled():
        with accounts.accounts_db() as db:
            tokens_table(db)
            row = db.execute('SELECT created FROM calendar_tokens WHERE user_id=?', (accounts.CURRENT_USER.get()['id'],)).fetchone()
        return {'active': bool(row), 'created': row['created'] if row else None}
    saved = core.setting('calendar_token', {})
    return {'active': bool(saved.get('hash')), 'created': saved.get('created')}


@router.post('/api/calendar/token', dependencies=[Depends(core.authorized)])
def new_calendar_link(request: Request, lang: str = 'ar'):
    """A new secret link; the old one stops working. Event titles follow the language it was made in."""
    token = secrets.token_urlsafe(24)
    core.set_setting('calendar_lang', 'en' if lang == 'en' else 'ar')
    if accounts.enabled():
        user = accounts.CURRENT_USER.get()
        with accounts.accounts_db() as db:
            tokens_table(db)
            db.execute('INSERT OR REPLACE INTO calendar_tokens VALUES (?,?,?)', (digest(token), user['id'], time.time()))
    else:
        core.set_setting('calendar_token', {'hash': digest(token), 'created': time.time()})
    url = f'{public_origin(request)}/calendar/{token}.ics'
    return {'url': url, 'webcal': 'webcal://' + url.split('://', 1)[1]}


@router.delete('/api/calendar/token', dependencies=[Depends(core.authorized)])
def stop_calendar_link():
    if accounts.enabled():
        with accounts.accounts_db() as db:
            tokens_table(db)
            db.execute('DELETE FROM calendar_tokens WHERE user_id=?', (accounts.CURRENT_USER.get()['id'],))
    else:
        core.set_setting('calendar_token', {})
    return {'active': False}


def owner_of(token):
    """The account a feed token belongs to (multi-user), True for the owner of a personal site, or None."""
    if not token or len(token) > 100:
        return None
    if accounts.enabled():
        with accounts.accounts_db() as db:
            tokens_table(db)
            row = db.execute('SELECT user_id FROM calendar_tokens WHERE token=?', (digest(token),)).fetchone()
        return accounts.user_by('id', row['user_id']) if row else None
    saved = core.setting('calendar_token', {}).get('hash', '')
    return True if saved and hmac.compare_digest(saved, digest(token)) else None


# ---------------------------------------------------------------- iCalendar text

def escape(text):
    return str(text or '').replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,').replace('\r', '').replace('\n', '\\n')


def fold(line):
    """RFC 5545: lines longer than 75 octets continue on the next line after a space."""
    raw, parts = line.encode(), []
    while len(raw) > 75:
        cut = 75 if not parts else 74
        while cut and (raw[cut] & 0xC0) == 0x80:  # never split a UTF-8 character
            cut -= 1
        parts.append(raw[:cut].decode())
        raw = raw[cut:]
    parts.append(raw.decode())
    return '\r\n '.join(parts)


def utc(moment):
    return moment.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def local(day, clock):
    return f"{day.replace('-', '')}T{clock.replace(':', '')}00"


def event(uid, summary, lines, description=''):
    out = ['BEGIN:VEVENT', f'UID:{uid}@studyhub', f'DTSTAMP:{utc(datetime.now(timezone.utc))}', f'SUMMARY:{escape(summary)}']
    out += lines
    if description:
        out.append(f'DESCRIPTION:{escape(description[:1500])}')
    return out + ['END:VEVENT']


def next_weekday(day_index):
    """The first date on/after today that falls on timetable day 0 (Sunday) … 6 (Saturday)."""
    today = datetime.now(CAIRO).date()
    return today + timedelta(days=(day_index - (today.weekday() + 1) % 7) % 7)


def build_feed():
    lang = core.setting('calendar_lang', 'ar')
    courses = {c['id']: c['title'] for c in core.rows('courses')}
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//StudyHub//Study calendar//AR', 'CALSCALE:GREGORIAN', 'METHOD:PUBLISH',
             'X-WR-CALNAME:StudyHub', 'X-WR-TIMEZONE:Africa/Cairo', 'REFRESH-INTERVAL;VALUE=DURATION:PT1H',
             'X-PUBLISHED-TTL:PT1H']
    horizon = datetime.now(timezone.utc) - timedelta(days=30)
    for task in core.rows('tasks'):
        if task.get('status') == 'done':
            continue
        course = courses.get(task.get('course_id'), '')
        if task.get('due'):
            due = datetime.fromisoformat(task['due'])
            if due > horizon:
                lines += event(f"due-{task['id']}", f"⏰ {task['title']}" + (f' · {course}' if course else ''),
                               [f'DTSTART:{utc(due - timedelta(minutes=30))}', f'DTEND:{utc(due)}'], task.get('details', ''))
        if task.get('planned_date') and task.get('planned_time'):
            start = datetime.fromisoformat(f"{task['planned_date']}T{task['planned_time']}").replace(tzinfo=CAIRO)
            end = start + timedelta(minutes=task.get('duration_minutes') or 25)
            lines += event(f"plan-{task['id']}", task['title'], [f'DTSTART;TZID=Africa/Cairo:{local(task["planned_date"], task["planned_time"])}',
                                                                 f'DTEND;TZID=Africa/Cairo:{end.strftime("%Y%m%dT%H%M%S")}'])
    for slot in core.rows('slots'):
        first = next_weekday(slot['day']).isoformat()
        title = f"{KIND[lang].get(slot.get('kind'), '')}: {slot['title']}".strip(': ')
        extra = [f'LOCATION:{escape(slot["location"])}'] if slot.get('location') else []
        lines += event(f"slot-{slot['id']}", title, [f'DTSTART;TZID=Africa/Cairo:{local(first, slot["start"])}',
                                                     f'DTEND;TZID=Africa/Cairo:{local(first, slot["end"])}', 'RRULE:FREQ=WEEKLY'] + extra)
    from .exams import label
    for exam in core.rows('exams'):
        if exam['date'] < (datetime.now(CAIRO).date() - timedelta(days=30)).isoformat():
            continue
        title = ('📝 Exam: ' if lang == 'en' else '📝 امتحان ') + label(exam, lang=lang)
        extra = [f'LOCATION:{escape(exam["location"])}'] if exam.get('location') else []
        if exam.get('time'):
            start = datetime.fromisoformat(f"{exam['date']}T{exam['time']}")
            end = start + timedelta(minutes=exam.get('duration_minutes') or 120)
            when = [f'DTSTART;TZID=Africa/Cairo:{local(exam["date"], exam["time"])}', f'DTEND;TZID=Africa/Cairo:{end.strftime("%Y%m%dT%H%M%S")}']
        else:
            day = date.fromisoformat(exam['date'])
            when = [f'DTSTART;VALUE=DATE:{day.strftime("%Y%m%d")}', f'DTEND;VALUE=DATE:{(day + timedelta(days=1)).strftime("%Y%m%d")}']
        lines += event(f"exam-{exam['id']}", title, when + extra)
    from .cp import calendar_contests
    for contest in calendar_contests():
        start = datetime.fromisoformat(contest['start'])
        lines += event(f"contest-{contest['id']}", f"🏁 {contest['name']}",
                       [f'DTSTART:{utc(start)}', f'DTEND:{utc(start + timedelta(minutes=contest["duration_minutes"]))}', f'URL:{contest["url"]}'],
                       contest['url'])
    lines.append('END:VCALENDAR')
    return '\r\n'.join(fold(line) for line in lines) + '\r\n'


@router.get('/calendar/{name}')
def feed(name: str):
    token = name[:-4] if name.endswith('.ics') else ''
    owner = owner_of(token)
    if not owner:
        raise HTTPException(404, 'Not found')
    if accounts.enabled():
        accounts.CURRENT_USER.set(owner)
    return Response(build_feed(), media_type='text/calendar; charset=utf-8',
                    headers={'Content-Disposition': 'inline; filename="studyhub.ics"', 'Cache-Control': 'private, max-age=900'})
