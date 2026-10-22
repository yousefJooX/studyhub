"""Flashcards with spaced repetition (an SM-2 variant, like Anki).

Cards come from three places:
  * typed in by hand,
  * pulled out of an Obsidian note that already uses the Spaced Repetition plugin syntax
    ("Question :: Answer", "Question ::: Answer" for both directions, or a question, a line with "?"
    and the answer, separated from the next card by a blank line),
  * generated from a note by the assistant model (the same OpenAI-compatible key as the chat).
Extracting and generating only return drafts; the student picks which ones to keep.

Each review grades the card again / hard / good / easy and moves its due date.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import accounts
from . import app as core

router = APIRouter(prefix='/api/cards', dependencies=[Depends(core.authorized)])
CAIRO = ZoneInfo('Africa/Cairo')
NOTE_LIMIT = 14_000          # characters of a note sent to the model


def today():
    return datetime.now(CAIRO).date()


class CardIn(BaseModel):
    front: str = Field(min_length=1, max_length=2000)
    back: str = Field(min_length=1, max_length=4000)
    course_id: str = ''
    source: str = Field(default='', max_length=500)


class CardBatch(BaseModel):
    cards: list[CardIn] = Field(min_length=1, max_length=200)


def new_card(body):
    return {'id': uuid.uuid4().hex, **body.model_dump(), 'front': body.front.strip(), 'back': body.back.strip(),
            'due': today().isoformat(), 'interval': 0, 'ease': 2.5, 'reps': 0, 'lapses': 0, 'created': time.time()}


@router.get('')
def list_cards(course_id: str = ''):
    cards = [c for c in core.rows('cards') if not course_id or c.get('course_id') == course_id]
    now = today().isoformat()
    cards.sort(key=lambda c: (c['due'], c.get('created', 0)))
    return {'cards': cards, 'due': sum(c['due'] <= now for c in cards), 'today': now,
            'learned': sum(c['reps'] > 0 for c in cards), 'reviewed_today': core.setting('card_reviews', {}).get(now, 0)}


@router.post('')
def add_cards(body: CardBatch):
    courses = {c['id'] for c in core.rows('courses')}
    if any(c.course_id and c.course_id not in courses for c in body.cards):
        raise HTTPException(404, 'المادة غير موجودة')
    existing = {(c['front'].strip().lower(), c.get('course_id', '')) for c in core.rows('cards')}
    added = []
    with core.database() as db:
        for card in body.cards:
            key = (card.front.strip().lower(), card.course_id)
            if key in existing:
                continue
            existing.add(key)
            value = new_card(card)
            db.execute('INSERT INTO cards(id,data) VALUES (?,?)', (value['id'], json.dumps(value, ensure_ascii=False)))
            added.append(value)
    return {'added': len(added), 'skipped': len(body.cards) - len(added), 'cards': added}


@router.put('/{card_id}')
def edit_card(card_id: str, body: CardIn):
    old = core.get('cards', card_id)
    core.check_course(body.course_id)
    return core.put('cards', old | {'front': body.front.strip(), 'back': body.back.strip(), 'course_id': body.course_id})


@router.delete('/{card_id}')
def delete_card(card_id: str):
    core.get('cards', card_id)
    with core.database() as db:
        db.execute('DELETE FROM cards WHERE id=?', (card_id,))
    return {'ok': True}


class Review(BaseModel):
    grade: str = Field(pattern='^(again|hard|good|easy)$')


def schedule(card, grade, on=None):
    """SM-2 with Anki-style buttons. 'again' keeps the card due today so it comes back in the same session."""
    on = on or today()
    ease, interval, reps = card.get('ease', 2.5), card.get('interval', 0), card.get('reps', 0)
    if grade == 'again':
        return card | {'ease': max(1.3, ease - 0.2), 'interval': 0, 'reps': 0, 'lapses': card.get('lapses', 0) + 1,
                       'due': on.isoformat(), 'last_review': on.isoformat()}
    if grade == 'hard':
        interval, ease = max(1, round(interval * 1.2)), max(1.3, ease - 0.15)
    elif grade == 'good':
        interval = 1 if reps == 0 else 3 if reps == 1 else max(interval + 1, round(interval * ease))
    else:
        interval, ease = (4 if reps == 0 else max(interval + 1, round(interval * ease * 1.3))), ease + 0.15
    interval = min(interval, 365)
    return card | {'ease': round(ease, 2), 'interval': interval, 'reps': reps + 1,
                   'due': (on + timedelta(days=interval)).isoformat(), 'last_review': on.isoformat()}


@router.post('/{card_id}/review')
def review(card_id: str, body: Review):
    card = schedule(core.get('cards', card_id), body.grade)
    core.put('cards', card)
    counts = core.setting('card_reviews', {})
    day = today().isoformat()
    counts = {k: v for k, v in counts.items() if k >= (today() - timedelta(days=90)).isoformat()} | {day: counts.get(day, 0) + 1}
    core.set_setting('card_reviews', counts)
    return card


# ---------------------------------------------------------------- drafts from a note

class Source(BaseModel):
    path: str = Field(default='', max_length=1000)     # an Obsidian note in the vault
    note_id: str = Field(default='', max_length=64)    # or a note written in StudyHub
    count: int = Field(default=10, ge=3, le=25)
    lang: str = Field(default='ar', pattern='^(ar|en)$')


def read_source(body):
    if body.note_id:
        note = core.get('notes', body.note_id)
        return note['title'], note.get('content', ''), note.get('course_id', ''), f"note:{note['id']}"
    if body.path:
        file = core.vault_note_file(body.path)
        relative = str(file.relative_to(core.vault_root()))
        course = next((c['id'] for c in core.rows('courses') if c.get('vault_folder') and
                       (relative + '/').startswith(c['vault_folder'].rstrip('/') + '/')), '')
        return file.stem, file.read_text(encoding='utf-8', errors='replace'), course, relative
    raise HTTPException(400, 'اختار نوت')


def clean(text):
    text = re.sub(r'<!--.*?-->|%%.*?%%', '', text, flags=re.S)
    return text.strip()


def extract(text):
    """Obsidian Spaced Repetition syntax → [{'front', 'back'}]."""
    text = clean(re.sub(r'^---\n.*?\n---\n', '', text, flags=re.S))
    cards, used = [], set()
    for number, line in enumerate(text.split('\n')):
        for marker, both in ((':::', True), ('::', False)):
            if marker in line and not line.lstrip().startswith(('```', '|')):
                front, _, back = line.partition(marker)
                front, back = front.strip(' -*>#\t'), back.strip()
                if front and back:
                    cards.append({'front': front, 'back': back})
                    if both:
                        cards.append({'front': back, 'back': front})
                    used.add(number)
                break
    for block in re.split(r'\n\s*\n', text):
        lines = block.strip('\n').split('\n')
        if '?' in [l.strip() for l in lines]:
            split = [l.strip() for l in lines].index('?')
            front, back = '\n'.join(lines[:split]).strip(' -#\n'), '\n'.join(lines[split + 1:]).strip()
            if front and back:
                cards.append({'front': front, 'back': back})
    return cards[:200]


def model_cards(title, text, count, lang):
    from .assistant import model_error, providers
    options = providers()
    if not options:
        raise HTTPException(503, 'توليد الكروت محتاج API key للمساعد. ضيفه من الإعدادات')
    if accounts.enabled():
        from .assistant import own_key
        if not own_key():  # the shared key: same daily budget as the chat
            cap = int(os.environ.get('STUDY_ASSISTANT_DAILY', '40'))
            usage, day = core.setting('assistant_usage', {}), today().isoformat()
            used = usage.get('count', 0) if usage.get('date') == day else 0
            if used >= cap:
                raise HTTPException(429, f'خلصت رسايل المساعد النهارده ({cap}). جرّب بكرة')
            core.set_setting('assistant_usage', {'date': day, 'count': used + 1})
    language = 'Egyptian Arabic, keeping technical terms in English' if lang == 'ar' else 'English'
    prompt = (f'Write {count} flashcards that test the important ideas of this study note. One fact or idea per card; '
              f'the question must make sense alone; answers short (1-3 sentences, formulas in LaTeX $...$ are fine). '
              f'Write them in {language}. Reply with JSON only: {{"cards": [{{"front": "...", "back": "..."}}]}}')
    messages = [{'role': 'system', 'content': prompt},
                {'role': 'user', 'content': f'# {title}\n\n{clean(text)[:NOTE_LIMIT]}'}]
    last = (502, 'الموديل مردّش')
    for base, key, model in options:
        body = json.dumps({'model': model, 'messages': messages, 'temperature': 0.3,
                           'response_format': {'type': 'json_object'}}).encode()
        request = urllib.request.Request(base + '/chat/completions', data=body, method='POST', headers={
            'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key, 'User-Agent': 'StudyHub/1.0'})
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                content = json.loads(response.read())['choices'][0]['message']['content']
        except urllib.error.HTTPError as error:
            last = model_error(error, model)
            continue
        except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as error:
            last = (502, f'مش قادر أوصل لمزوّد الموديل: {error}')
            continue
        match = re.search(r'\{.*\}', content or '', re.S)
        try:
            items = json.loads(match.group(0))['cards'] if match else []
        except (ValueError, KeyError, TypeError):
            items = []
        cards = [{'front': str(c.get('front', '')).strip()[:2000], 'back': str(c.get('back', '')).strip()[:4000]}
                 for c in items if isinstance(c, dict)]
        cards = [c for c in cards if c['front'] and c['back']]
        if cards:
            return cards[:count]
        last = (502, f'{model}: الرد ماكانش فيه كروت. جرّب تاني')
    raise HTTPException(*last)


@router.post('/extract')
def extract_drafts(body: Source):
    title, text, course, source = read_source(body)
    return {'title': title, 'course_id': course, 'source': source, 'cards': extract(text)}


@router.post('/generate')
def generate_drafts(body: Source):
    title, text, course, source = read_source(body)
    if len(clean(text)) < 80:
        raise HTTPException(400, 'النوت قصيرة جدًا عشان يطلع منها كروت')
    user = accounts.CURRENT_USER.get()
    accounts.throttle('cards-ai:' + (user['id'] if user else 'owner'), 20, 3600)
    return {'title': title, 'course_id': course, 'source': source, 'cards': model_cards(title, text, body.count, body.lang)}
