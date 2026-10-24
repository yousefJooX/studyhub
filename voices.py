"""Deadline reminder stages (e.g. 1 day / 12 h / 1 h before a task's deadline) with the owner's
own voice clips. Each stage has a folder of clips; every reminder plays a random clip from it.
One more stage, "done", is not a reminder: one of its clips plays when the user finishes a task."""
import json
import random
import re
import time
import uuid
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from .app import authorized, data_root, database, setting

router = APIRouter(prefix='/api/voices', dependencies=[Depends(authorized)])
AUDIO = {'.mp3': 'audio/mpeg', '.m4a': 'audio/mp4', '.aac': 'audio/aac', '.ogg': 'audio/ogg', '.oga': 'audio/ogg',
         '.opus': 'audio/ogg', '.wav': 'audio/wav', '.webm': 'audio/webm'}
MAX_BYTES = 10 * 1024 * 1024
DEFAULT_STAGES = [
    {'id': 'day', 'minutes': 1440, 'title': 'فاضل يوم على الديدلاين'},
    {'id': 'half-day', 'minutes': 720, 'title': 'فاضل ١٢ ساعة على الديدلاين'},
    {'id': 'hour', 'minutes': 60, 'title': 'فاضل ساعة على الديدلاين'},
]


DONE_STAGE = {'id': 'done', 'minutes': None, 'title': 'لما تخلّص مهمة'}


def stages():
    """The deadline reminder stages (the "done" stage is not one of them)."""
    return setting('deadline_stages', DEFAULT_STAGES)


def all_stages():
    return stages() + [DONE_STAGE]


def stage(stage_id):
    found = next((s for s in all_stages() if s['id'] == stage_id), None)
    if not found:
        raise HTTPException(404, 'المرحلة دي مش موجودة')
    return found


def folder(stage_id):
    path = data_root() / 'voices' / stage_id
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def clips(stage_id):
    return sorted((p for p in folder(stage_id).iterdir() if p.is_file() and p.suffix.lower() in AUDIO),
                  key=lambda p: p.stat().st_mtime)


def display_name(path):
    return Path(path.name.split('__', 1)[-1]).stem


def pick_voice(stage_id):
    """URL of a random clip for this stage, or '' when the folder is empty."""
    files = clips(stage_id)
    return f'/api/voices/{stage_id}/{quote(random.choice(files).name)}' if files else ''


def deadline_events(task, now):
    """The most recent deadline stage that has passed (once per stage and deadline, only for open tasks).
    Stages that were already over when the task was created are skipped, so a new task with a close
    deadline doesn't get a burst of old reminders."""
    if task.get('status') == 'done' or not task.get('due'):
        return []
    from datetime import datetime
    try:
        due = datetime.fromisoformat(task['due']).timestamp()
    except ValueError:
        return []
    created = task.get('created', 0)
    passed = []
    for s in stages():
        at = due - s['minutes'] * 60
        if at <= now and now - at <= 6 * 3600 and at >= created - 60 and now < due + 3600:
            passed.append((at, s))
    if not passed:
        return []
    at, s = max(passed, key=lambda p: p[0])
    return [('deadline:' + s['id'], task['due'], at)]


@router.get('')
def list_voices():
    return {'stages': [{**s, 'clips': [{'file': p.name, 'name': display_name(p), 'size': p.stat().st_size,
                                        'url': f'/api/voices/{s["id"]}/{quote(p.name)}'} for p in clips(s['id'])]}
                       for s in all_stages()]}


@router.get('/pick/{stage_id}')
def pick(stage_id: str):
    """A random clip of a stage (the app plays one from "done" when a task is finished). Before /{stage_id}/{file}."""
    stage(stage_id)
    return {'url': pick_voice(stage_id)}


@router.post('/{stage_id}')
async def upload_voice(stage_id: str, request: Request, name: str = 'voice.webm'):
    stage(stage_id)
    original = Path(name.replace('\\', '/')).name[:80] or 'voice.webm'
    suffix = Path(original).suffix.lower()
    if suffix not in AUDIO:
        raise HTTPException(400, 'ارفع ملف صوت: mp3 أو m4a أو ogg أو wav أو webm')
    safe = re.sub(r'[^\w.\- ]+', '_', original).strip() or 'voice' + suffix
    from .accounts import remaining_bytes
    left = remaining_bytes()
    limit = MAX_BYTES if left is None else min(MAX_BYTES, left)
    target = folder(stage_id) / f'{uuid.uuid4().hex[:10]}__{safe}'
    size = 0
    try:
        with target.open('xb') as stream:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, 'الفويس لازم يكون أقل من ١٠ ميجا')
                stream.write(chunk)
        if size == 0:
            raise HTTPException(400, 'الملف فاضي')
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return {'file': target.name, 'name': display_name(target), 'url': f'/api/voices/{stage_id}/{quote(target.name)}'}


def clip_path(stage_id, file):
    stage(stage_id)
    path = folder(stage_id) / file
    if '/' in file or '\\' in file or file.startswith('.') or not path.is_file() or path.suffix.lower() not in AUDIO:
        raise HTTPException(404, 'الفويس مش موجود')
    return path


@router.get('/{stage_id}/{file}')
def play_voice(stage_id: str, file: str):
    path = clip_path(stage_id, file)
    return FileResponse(path, media_type=AUDIO[path.suffix.lower()], headers={'Cache-Control': 'private, max-age=86400'})


@router.delete('/{stage_id}/{file}')
def delete_voice(stage_id: str, file: str):
    clip_path(stage_id, file).unlink()
    return {'ok': True}


@router.post('/{stage_id}/test')
def test_stage(stage_id: str):
    """Send this stage's reminder (with a random clip) to every device that enabled notifications."""
    from .reminders import send_push
    s = stage(stage_id)
    if s['minutes'] is None:
        raise HTTPException(400, 'المرحلة دي بتشتغل لما تخلّص مهمة، مش تذكير')
    with database() as db:
        subscriptions = [json.loads(r['data']) for r in db.execute('SELECT data FROM push_subscriptions')]
    if not subscriptions:
        raise HTTPException(404, 'فعّل إشعارات الجهاز الأول من الإعدادات')
    payload = {'title': 'مساحتي — ' + s['title'], 'body': 'تجربة: ده شكل تذكير المرحلة دي.', 'tag': 'studyhub-stage-test-' + stage_id,
               'url': '/', 'voice': pick_voice(stage_id), 'stage': stage_id}
    sent = 0
    for subscription in subscriptions:
        try:
            send_push(subscription, payload)
            sent += 1
        except Exception:
            pass
    if not sent:
        raise HTTPException(502, 'تعذّر إرسال الإشعار؛ أعد تفعيل إشعارات الجهاز')
    return {'sent': sent, 'voice': payload['voice'], 'at': time.time()}
