"""Persistent reminder outbox and encrypted Web Push for opted-in devices."""
import asyncio
import base64
import hashlib
import json
import logging
import os
import threading
import time
from datetime import datetime
from urllib.parse import urlparse

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from .app import authorized, data_root, database, setting, set_setting, settle_timers

router = APIRouter(prefix='/api/notifications/push', dependencies=[Depends(authorized)])
key_lock = threading.Lock()


def b64(data):
    return base64.urlsafe_b64encode(data).decode().rstrip('=')


def key_pair():
    from .accounts import base_root
    path = base_root() / 'webpush-private.pem'  # one VAPID key for the whole site
    with key_lock:
        if not path.exists():
            key = ec.generate_private_key(ec.SECP256R1())
            encoded = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption())
            with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
                stream.write(encoded)
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    public = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return str(path), b64(public)


def valid_endpoint(value):
    parsed = urlparse(value)
    host = parsed.hostname or ''
    allowed = host in {'fcm.googleapis.com', 'updates.push.services.mozilla.com'} or host.endswith('.push.apple.com') or host.endswith('.notify.windows.com')
    if parsed.scheme != 'https' or not allowed or parsed.username or parsed.password or parsed.port not in (None, 443) or parsed.fragment:
        raise ValueError('Use a supported browser HTTPS push endpoint')
    return value


class Endpoint(BaseModel):
    endpoint: str = Field(max_length=4096)
    _endpoint = field_validator('endpoint')(valid_endpoint)


class PushKeys(BaseModel):
    p256dh: str = Field(max_length=200)
    auth: str = Field(max_length=100)

    @field_validator('p256dh', 'auth')
    @classmethod
    def valid_key(cls, value, info):
        try:
            raw = base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)
            if info.field_name == 'p256dh':
                ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
            elif len(raw) != 16:
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError('Invalid browser subscription key')
        return value


class Subscription(Endpoint):
    keys: PushKeys


def subscription_id(endpoint):
    return hashlib.sha256(endpoint.encode()).hexdigest()


@router.get('/config')
def configuration():
    _, public = key_pair()
    with database() as db:
        devices = db.execute('SELECT COUNT(*) FROM push_subscriptions').fetchone()[0]
        failed = db.execute("SELECT COUNT(*) FROM reminder_deliveries WHERE status='failed' AND due>?", (time.time() - 86400,)).fetchone()[0]
    return {'public_key': public, 'devices': devices, 'worker': setting('reminder_worker', {}), 'failed_recent': failed}


@router.post('/subscription')
def subscribe(body: Subscription):
    value = body.model_dump()
    key = subscription_id(body.endpoint)
    with database() as db:
        old = db.execute('SELECT data FROM push_subscriptions WHERE id=?', (key,)).fetchone()
        previous = json.loads(old['data']) if old else {}
        value['created'] = previous.get('created', time.time())
        value['last_test'] = previous.get('last_test', 0)
        db.execute('INSERT OR REPLACE INTO push_subscriptions VALUES (?,?)', (key, json.dumps(value)))
    return {'ok': True}


@router.delete('/subscription')
def unsubscribe(body: Endpoint):
    key = subscription_id(body.endpoint)
    with database() as db:
        db.execute('DELETE FROM push_subscriptions WHERE id=?', (key,))
        db.execute('DELETE FROM reminder_deliveries WHERE subscription_id=?', (key,))
    return {'ok': True}


def send_push(subscription, payload):
    import requests
    from pywebpush import webpush

    class NoRedirectSession(requests.Session):
        def request(self, *args, **kwargs):
            kwargs['allow_redirects'] = False
            return super().request(*args, **kwargs)

    valid_endpoint(subscription['endpoint'])
    private, _ = key_pair()
    origin = os.environ.get('STUDY_PUBLIC_ORIGIN') or 'mailto:admin@example.com'
    with NoRedirectSession() as session:
        webpush(subscription_info={'endpoint': subscription['endpoint'], 'keys': subscription['keys']},
                data=json.dumps(payload, ensure_ascii=False), vapid_private_key=private,
                vapid_claims={'sub': origin}, ttl=3600, timeout=8, requests_session=session,
                headers={'Urgency': 'high'})


@router.post('/test')
def test_notification(body: Endpoint):
    key = subscription_id(body.endpoint)
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT data FROM push_subscriptions WHERE id=?', (key,)).fetchone()
        if not row:
            raise HTTPException(404, 'فعّل إشعارات الجهاز الأول')
        subscription = json.loads(row['data'])
        if subscription.get('last_test', 0) > time.time() - 30:
            raise HTTPException(429, 'استنى نصف دقيقة قبل التجربة تاني')
        subscription['last_test'] = time.time()
        db.execute('UPDATE push_subscriptions SET data=? WHERE id=?', (json.dumps(subscription), key))
    try:
        send_push(subscription, {'title': 'مساحتي — الإشعارات جاهزة', 'body': 'ده إشعار تجريبي لتذكيرات مهامك والتايمر.', 'tag': 'studyhub-test', 'url': '/'})
    except Exception:
        raise HTTPException(502, 'تعذّر إرسال إشعار التجربة؛ حاول تاني أو أعد تفعيل الجهاز')
    return {'accepted': True}


def task_events(task, now):
    if task.get('status') == 'done':
        return []
    events = []
    if task.get('remind_at'):
        due = datetime.fromisoformat(task['remind_at']).timestamp()
        if now - 86400 <= due <= now:
            events.append(('reminder', task['remind_at'], due))
    timer = task.get('timer', {})
    if timer.get('state') == 'finished' and timer.get('run_id') and timer.get('finished_at', 0) >= now - 86400:
        events.append(('timer', timer['run_id'], timer['finished_at']))
    from .voices import deadline_events
    events += deadline_events(task, now)
    return events


def collect_jobs(now):
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        subscriptions = db.execute('SELECT id FROM push_subscriptions').fetchall()
        for row in db.execute('SELECT data FROM tasks').fetchall():
            task = json.loads(row['data'])
            timer = task.get('timer', {})
            if timer.get('state') == 'running' and timer['ends_at'] <= now:
                task['timer'] = timer | {'state': 'finished', 'remaining_seconds': 0, 'finished_at': timer['ends_at']}
                db.execute('UPDATE tasks SET data=? WHERE id=?', (json.dumps(task, ensure_ascii=False), task['id']))
            for kind, stamp, due in task_events(task, now):
                for subscription in subscriptions:
                    key = hashlib.sha256(json.dumps([task['id'], kind, stamp, subscription['id']]).encode()).hexdigest()
                    db.execute('INSERT OR IGNORE INTO reminder_deliveries(id,task_id,subscription_id,kind,stamp,due) VALUES (?,?,?,?,?,?)',
                               (key, task['id'], subscription['id'], kind, stamp, due))
        db.execute("DELETE FROM reminder_deliveries WHERE due<? AND status<>'pending'", (now - 7 * 86400,))


def dispatch_job(job_id, now):
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute("SELECT * FROM reminder_deliveries WHERE id=? AND status='pending' AND next_try<=?", (job_id, now)).fetchone()
        if not row:
            return
        job = dict(row)
        task_row = db.execute('SELECT data FROM tasks WHERE id=?', (job['task_id'],)).fetchone()
        sub_row = db.execute('SELECT data FROM push_subscriptions WHERE id=?', (job['subscription_id'],)).fetchone()
        task = json.loads(task_row['data']) if task_row else {}
        if not sub_row or not any(kind == job['kind'] and stamp == job['stamp'] for kind, stamp, _ in task_events(task, now)):
            db.execute("UPDATE reminder_deliveries SET status='cancelled' WHERE id=?", (job_id,))
            return
        subscription = json.loads(sub_row['data'])
        db.execute('UPDATE reminder_deliveries SET attempts=attempts+1,next_try=? WHERE id=?', (now + 120, job_id))
    payload = {'title': 'مساحتي — وقت الجلسة خلص' if job['kind'] == 'timer' else 'مساحتي — تذكير مهمة',
               'body': task['title'], 'tag': 'studyhub-' + job_id, 'url': '/?task=' + task['id']}
    if job['kind'].startswith('deadline:'):
        from .voices import pick_voice, stages
        stage_id = job['kind'].split(':', 1)[1]
        current = next((s for s in stages() if s['id'] == stage_id), {'title': 'الديدلاين قرّب'})
        payload |= {'title': 'مساحتي — ' + current['title'], 'tag': 'studyhub-deadline-' + task['id'],
                    'voice': pick_voice(stage_id), 'stage': stage_id}
    elif job['kind'] == 'timer':
        from .voices import pick_voice
        payload['voice'] = pick_voice('done')  # your "finished" clip, when you added one
    try:
        send_push(subscription, payload)
        result = 'sent'
    except Exception as error:
        response = getattr(error, 'response', None)
        status_code = getattr(response, 'status_code', None)
        if status_code in (404, 410):
            with database() as db:
                db.execute('DELETE FROM push_subscriptions WHERE id=?', (job['subscription_id'],))
            result = 'cancelled'
        else:
            result = 'failed' if job['attempts'] >= 7 else 'pending'
    with database() as db:
        db.execute('UPDATE reminder_deliveries SET status=?,next_try=? WHERE id=?',
                   (result, now + min(30 * 2 ** job['attempts'], 3600), job_id))


def run_once(now=None):
    now = time.time() if now is None else now
    key_pair()
    collect_jobs(now)
    with database() as db:
        pending = db.execute("SELECT id FROM reminder_deliveries WHERE status='pending' AND next_try<=? ORDER BY due LIMIT 20", (now,)).fetchall()
    for job in pending:
        dispatch_job(job['id'], now)
    set_setting('reminder_worker', {'last_tick': time.time(), 'ok': True})


async def reminder_loop():
    from . import accounts
    from .board import sync_quietly as board_sync_quietly
    while True:
        try:
            if accounts.enabled():
                for user in await asyncio.to_thread(accounts.all_users):
                    token = accounts.CURRENT_USER.set(user)
                    try:
                        await asyncio.to_thread(settle_timers)
                        await asyncio.to_thread(run_once)
                        await asyncio.to_thread(board_sync_quietly)
                    except Exception as error:
                        logging.warning('StudyHub reminders for a user: %s', type(error).__name__)
                    finally:
                        accounts.CURRENT_USER.reset(token)
            else:
                await asyncio.to_thread(settle_timers)
                await asyncio.to_thread(run_once)
                await asyncio.to_thread(board_sync_quietly)
        except Exception as error:
            logging.warning('StudyHub reminder worker: %s', type(error).__name__)
            try:
                set_setting('reminder_worker', {'last_tick': time.time(), 'ok': False})
            except Exception:
                pass
        await asyncio.sleep(10)
