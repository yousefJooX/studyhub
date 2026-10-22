"""Share a course with classmates on the friends site (STUDY_MULTIUSER=1).

The owner makes a share code for one course and sends it to their friends. Whoever enters the code
gets their OWN copy of the course as it is at that moment: its notes, open tasks, timetable slots,
upcoming exams and flashcards (fresh, unreviewed). Nothing stays linked, so nobody can see or change
the other's space; entering the code again later brings a new copy with whatever was added since.
Obsidian vault files and uploaded files are personal and are not copied.

Codes expire after SHARE_DAYS and the owner can stop a code at any time. Only the code's SHA-256 is
kept in the shared accounts database; the owner's own space remembers the code so they can see it again.
"""
import json
import secrets
import sqlite3
import time
import uuid
from contextlib import closing
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException

from . import accounts
from . import app as core

router = APIRouter(dependencies=[Depends(core.authorized)])
SHARE_DAYS = 30
ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'   # no 0/O or 1/I
CAIRO = ZoneInfo('Africa/Cairo')


def multiuser_only():
    if not accounts.enabled():
        raise HTTPException(404, 'مشاركة المواد متاحة على موقع الأصحاب بس')
    return accounts.CURRENT_USER.get()


def table(db):
    db.execute('CREATE TABLE IF NOT EXISTS course_shares(code TEXT PRIMARY KEY, user_id TEXT NOT NULL, '
               'course_id TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL)')


def normalise(code):
    return ''.join(ch for ch in str(code or '').upper() if ch in ALPHABET)[:12]


@router.get('/api/courses/{course_id}/share')
def share_status(course_id: str):
    multiuser_only()
    core.get('courses', course_id)
    saved = core.setting('course_shares', {}).get(course_id)
    if not saved or saved['expires'] < time.time():
        return {'active': False}
    return {'active': True, 'code': saved['code'], 'expires': saved['expires']}


@router.post('/api/courses/{course_id}/share')
def make_share(course_id: str):
    user = multiuser_only()
    core.get('courses', course_id)
    accounts.throttle('share:' + user['id'], 30, 3600)
    code = ''.join(secrets.choice(ALPHABET) for _ in range(8))
    now, expires = time.time(), time.time() + SHARE_DAYS * 86400
    shares = core.setting('course_shares', {})
    with accounts.accounts_db() as db:
        table(db)
        if shares.get(course_id):  # one live code per course
            db.execute('DELETE FROM course_shares WHERE code=?', (accounts.digest(shares[course_id]['code']),))
        db.execute('INSERT INTO course_shares VALUES (?,?,?,?,?)', (accounts.digest(code), user['id'], course_id, now, expires))
    core.set_setting('course_shares', shares | {course_id: {'code': code, 'expires': expires}})
    return {'active': True, 'code': code, 'expires': expires}


@router.delete('/api/courses/{course_id}/share')
def stop_share(course_id: str):
    multiuser_only()
    shares = core.setting('course_shares', {})
    if shares.get(course_id):
        with accounts.accounts_db() as db:
            table(db)
            db.execute('DELETE FROM course_shares WHERE code=?', (accounts.digest(shares[course_id]['code']),))
        shares.pop(course_id)
        core.set_setting('course_shares', shares)
    return {'active': False}


def lookup(code):
    user = multiuser_only()
    accounts.throttle('share-code:' + user['id'], 40, 3600)  # codes can't be guessed by trying many
    code = normalise(code)
    if len(code) != 8:
        raise HTTPException(404, 'الكود ده مش صحيح')
    with accounts.accounts_db() as db:
        table(db)
        row = db.execute('SELECT * FROM course_shares WHERE code=? AND expires>?', (accounts.digest(code), time.time())).fetchone()
    if not row:
        raise HTTPException(404, 'الكود ده مش صحيح أو انتهى')
    owner = accounts.user_by('id', row['user_id'])
    if not owner:
        raise HTTPException(404, 'الكود ده مش صحيح أو انتهى')
    return dict(row), owner, user


def snapshot(owner_id, course_id):
    """Read the owner's course straight from their database, read-only."""
    path = accounts.user_root(owner_id) / 'study.sqlite3'
    with closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=20)) as db:
        def load(name):
            try:
                return [json.loads(r[0]) for r in db.execute(f'SELECT data FROM {name}')]
            except sqlite3.OperationalError:  # an older space without that table
                return []
        course = next((c for c in load('courses') if c['id'] == course_id), None)
        if not course:
            raise HTTPException(404, 'المادة دي اتمسحت عند صاحبها')
        today = datetime.now(CAIRO).date().isoformat()
        mine = lambda rows: [r for r in rows if r.get('course_id') == course_id]  # noqa: E731
        return {'course': {'title': course['title'], 'kind': course.get('kind', 'personal')},
                'notes': [{'title': n['title'], 'content': n.get('content', '')} for n in mine(load('notes'))],
                'tasks': [{k: t.get(k, '') for k in ('title', 'details', 'due', 'priority')} for t in mine(load('tasks'))
                          if t.get('status') != 'done' and not t.get('board') and not t.get('plan')],
                'slots': [{k: s.get(k, '') for k in ('title', 'kind', 'day', 'start', 'end', 'location')} for s in mine(load('slots'))],
                'exams': [{k: e.get(k) for k in ('title', 'kind', 'date', 'time', 'duration_minutes', 'location', 'weight')}
                          for e in mine(load('exams')) if e['date'] >= today],
                'cards': [{'front': c['front'], 'back': c['back']} for c in mine(load('cards'))]}


@router.get('/api/shares/{code}')
def preview(code: str):
    row, owner, user = lookup(code)
    data = snapshot(owner['id'], row['course_id'])
    return {'course': data['course']['title'], 'owner': owner['name'], 'own': owner['id'] == user['id'],
            'counts': {k: len(data[k]) for k in ('notes', 'tasks', 'slots', 'exams', 'cards')}}


@router.post('/api/shares/{code}/import')
def import_share(code: str):
    row, owner, user = lookup(code)
    if owner['id'] == user['id']:
        raise HTTPException(400, 'دي مادتك إنت أصلًا')
    data = snapshot(owner['id'], row['course_id'])
    now = time.time()
    course = core.put('courses', {'id': uuid.uuid4().hex, 'title': data['course']['title'], 'kind': data['course']['kind'],
                                  'vault_folder': '', 'created': now, 'shared_by': owner['name']})
    for note in data['notes']:
        core.save_entry('notes', core.Note(title=note['title'][:200], course_id=course['id'], content=note['content']))
    for task in data['tasks']:
        core.save_entry('tasks', core.Task(title=task['title'][:300], course_id=course['id'], due=task['due'] or '',
                                           priority=task['priority'] or 'normal', details=task['details'] or ''))
    for slot in data['slots']:
        core.save_entry('slots', core.Slot(**slot, course_id=course['id']))
    from .exams import Exam, save as save_exam
    for exam in data['exams']:
        save_exam(Exam(**{k: v for k, v in exam.items() if v is not None}, course_id=course['id']))
    if data['cards']:
        from .cards import CardBatch, CardIn, add_cards
        for start in range(0, len(data['cards']), 200):
            add_cards(CardBatch(cards=[CardIn(**c, course_id=course['id'], source='shared') for c in data['cards'][start:start + 200]]))
    return {'course_id': course['id'], 'title': course['title'], 'counts': {k: len(data[k]) for k in ('notes', 'tasks', 'slots', 'exams', 'cards')}}
