"""In-app assistant: chat with an OpenAI-compatible model (Groq, Gemini, OpenRouter, Ollama…)
that can use the same StudyHub tools as the MCP server.

Config (data/config.json, set with `python -m studyhub llm`):
  STUDY_LLM_API_KEY   provider API key (empty for a local Ollama)
  STUDY_LLM_BASE_URL  default https://api.groq.com/openai/v1
  STUDY_LLM_MODEL     default llama-3.3-70b-versatile
"""
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import app as core
from .mcp_server import CAIRO, TOOL_INDEX, TOOLS

router = APIRouter()
MAX_ROUNDS = 8
DEFAULT_BASE = 'https://api.groq.com/openai/v1'
DEFAULT_MODEL = 'llama-3.3-70b-versatile'


class ChatMessage(BaseModel):
    role: str = Field(pattern='^(user|assistant)$')
    content: str = Field(max_length=8000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=40)
    lang: str = Field(default='ar', pattern='^(ar|en)$')


def settings():
    return (os.environ.get('STUDY_LLM_BASE_URL', DEFAULT_BASE).rstrip('/'),
            os.environ.get('STUDY_LLM_API_KEY', ''),
            os.environ.get('STUDY_LLM_MODEL', DEFAULT_MODEL))


# Providers a user can pick for their own key (Settings → المساعد).
USER_PROVIDERS = {
    'groq': {'name': 'Groq', 'base': 'https://api.groq.com/openai/v1', 'model': 'openai/gpt-oss-120b',
             'keys_url': 'https://console.groq.com/keys', 'prefix': 'gsk_'},
    'gemini': {'name': 'Google Gemini', 'base': 'https://generativelanguage.googleapis.com/v1beta/openai',
               'model': 'gemini-3.8-flash', 'keys_url': 'https://aistudio.google.com/apikey', 'prefix': 'AIza'},
    'openrouter': {'name': 'OpenRouter', 'base': 'https://openrouter.ai/api/v1', 'model': 'openai/gpt-oss-120b:free',
                   'keys_url': 'https://openrouter.ai/keys', 'prefix': 'sk-or-'},
}


def provider_base(provider):
    # STUDY_PROVIDER_BASE_<NAME> overrides a provider's address (tests, self-hosted gateways).
    return os.environ.get('STUDY_PROVIDER_BASE_' + provider.upper(), USER_PROVIDERS[provider]['base']).rstrip('/')


def own_key():
    """(provider, base, key, model) of the current user's own API key, or None."""
    from .keystore import unseal
    saved = core.setting('own_llm', {})
    if saved.get('provider') not in USER_PROVIDERS or not saved.get('key'):
        return None
    key = unseal(saved['key'])
    return (saved['provider'], provider_base(saved['provider']), key, saved.get('model') or USER_PROVIDERS[saved['provider']]['model']) if key else None


def providers():
    """The user's own key first (if they added one), then the site's model and its optional fallback."""
    found = []
    mine = own_key()
    if mine:
        found.append(mine[1:])
    for prefix in ('STUDY_LLM', 'STUDY_LLM_FALLBACK'):
        base = os.environ.get(prefix + '_BASE_URL', DEFAULT_BASE if prefix == 'STUDY_LLM' else '').rstrip('/')
        key = os.environ.get(prefix + '_API_KEY', '')
        model = os.environ.get(prefix + '_MODEL', DEFAULT_MODEL if prefix == 'STUDY_LLM' else '')
        local = 'localhost' in base or '127.0.0.1' in base
        if base and model and (key or local):
            found.append((base, key, model))
    return found


def system_prompt(lang):
    now = datetime.now(CAIRO)
    courses = core.rows('courses')
    course_lines = '\n'.join(f'- {c["title"]} ({c.get("kind", "")})' for c in sorted(courses, key=lambda c: c['title'])) or '- (no courses yet)'
    language = ('Reply in casual Egyptian Arabic, keeping technical terms in English.' if lang == 'ar'
                else 'Reply in English.')
    owner = core.current_user_public().get('name') or 'the user'
    return f"""You are the assistant inside StudyHub, the personal study workspace of {owner} (a university student).
Now: {now.strftime('%A %Y-%m-%d %H:%M')} Africa/Cairo. Study weeks run Saturday to Friday.
{language} Be short and practical.

His courses:
{course_lines}

How to work:
- Use the tools to read or change real data. Never invent tasks, deadlines or note content.
- When they list several things to do ("عندي كذا وكذا وآخر معاد كذا"), create one task per item with create_task.
  Map each item to the right course by name (partial names are fine). Turn relative dates (بكرة، السبت الجاي، آخر الأسبوع)
  into real dates from today's date. If an item clearly belongs to a course that does not exist, create the course first.
- Lecture/section/lab times they give go into the weekly timetable with create_slot.
- For questions about course material, search_notes then read_note, and mention which note you used.
- If something important is ambiguous (which course, which date), ask one short question instead of guessing.
- After changing data, finish with a short list of exactly what you created or changed."""


def openai_tools():
    return [{'type': 'function', 'function': {'name': t['name'], 'description': t['description'],
                                               'parameters': t['inputSchema']}} for t in TOOLS]


def request_model(base, key, model, messages):
    body = json.dumps({'model': model, 'messages': messages, 'tools': openai_tools(),
                       'tool_choice': 'auto', 'temperature': 0.2}).encode()
    request = urllib.request.Request(base + '/chat/completions', data=body, method='POST', headers={
        'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key, 'User-Agent': 'StudyHub/1.0'})
    with urllib.request.urlopen(request, timeout=90) as response:
        return json.loads(response.read())['choices'][0]['message']


def call_model(messages):
    options = providers()
    if not options:
        raise HTTPException(503, 'المساعد محتاج API key. شغّل على السيرفر: python -m studyhub llm')
    last = None
    for base, key, model in options:
        for attempt in range(3):
            try:
                return request_model(base, key, model, messages)
            except urllib.error.HTTPError as error:
                if error.code in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(1.5 * (attempt + 1))  # busy / overloaded: short retry before falling back
                    continue
                last = model_error(error, model)
                break
            except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as error:
                last = (502, f'مش قادر أوصل لمزوّد الموديل: {error}')
                break
    raise HTTPException(*last)


def model_error(error, model):
    raw = error.read().decode(errors='replace')
    try:
        parsed = json.loads(raw)
        parsed = parsed[0] if isinstance(parsed, list) and parsed else parsed
        detail = (parsed.get('error') or {}).get('message') or raw
    except (ValueError, AttributeError):
        detail = raw
    if error.code == 429:
        return 429, f'{model}: وصلت للحد المجاني دلوقتي. استنى شوية وجرب تاني.'
    if error.code == 503:
        return 503, f'{model}: الموديل عليه ضغط دلوقتي. جرّب كمان دقيقة، أو ضيف موديل احتياطي.'
    return 502, f'{model} ({error.code}): ' + detail[:240]


def run_tool(name, raw_args):
    tool = TOOL_INDEX.get(name)
    if not tool:
        return {'ok': False, 'error': f'Unknown tool {name}'}, {}
    try:
        args = json.loads(raw_args or '{}') if isinstance(raw_args, str) else (raw_args or {})
    except ValueError:
        return {'ok': False, 'error': 'Arguments were not valid JSON'}, {}
    try:
        return {'ok': True, 'result': tool['handler'](args)}, args
    except HTTPException as error:
        return {'ok': False, 'error': error.detail if isinstance(error.detail, str) else 'Rejected'}, args
    except (ValueError, KeyError, TypeError) as error:
        return {'ok': False, 'error': str(error) or error.__class__.__name__}, args


def describe(name, args, outcome):
    """Short human line for the chat UI about a write action."""
    if not outcome.get('ok'):
        return None
    result = outcome['result']
    item = result.get('created') or result.get('updated') or result.get('exists') or {}
    title = item.get('title', '')
    labels = {'create_task': 'مهمة جديدة', 'update_task': 'اتعدّلت مهمة', 'create_note': 'نوت جديدة',
              'create_course': 'مادة جديدة' if 'created' in result else 'المادة موجودة', 'create_slot': 'ميعاد في الجدول',
              'create_exam': 'امتحان جديد', 'update_exam': 'اتعدّل امتحان', 'delete_exam': 'اتمسح امتحان',
              'create_flashcards': 'كروت جديدة', 'review_flashcard': 'اتراجع كارت', 'update_slot': 'اتعدّل ميعاد',
              'delete_slot': 'اتشال ميعاد', 'task_timer': 'التايمر'}
    if name == 'plan_study' and result.get('applied'):
        return {'tool': name, 'label': 'خطة المذاكرة', 'title': f"{result.get('created_tasks', 0)} جلسة", 'detail': f"{result.get('from', '')} → {result.get('to', '')}"}
    item = item or result.get('deleted') or {}
    title = item.get('title') or item.get('label', '')
    if name not in labels:
        return None
    extra = ''
    if name == 'create_task' and item.get('due'):
        extra = f" · {item['due']}"
    if name == 'create_slot':
        extra = f" · {item.get('day', '')} {item.get('start', '')}–{item.get('end', '')}"
    return {'tool': name, 'label': labels[name], 'title': title, 'detail': (item.get('course') or '') + extra}


@router.post('/api/assistant', dependencies=[Depends(core.authorized)])
def assistant(body: ChatRequest):
    from . import accounts
    if accounts.enabled() and not own_key():  # shared key: keep each user to a daily budget
        cap = int(os.environ.get('STUDY_ASSISTANT_DAILY', '40'))
        today = datetime.now(CAIRO).date().isoformat()
        usage = core.setting('assistant_usage', {})
        used = usage.get('count', 0) if usage.get('date') == today else 0
        if used >= cap:
            raise HTTPException(429, f'خلصت رسايل المساعد النهارده ({cap}). جرّب بكرة، أو استخدم Claude عن طريق MCP من الإعدادات')
        core.set_setting('assistant_usage', {'date': today, 'count': used + 1})
    messages = [{'role': 'system', 'content': system_prompt(body.lang)}]
    messages += [{'role': m.role, 'content': m.content} for m in body.messages[-20:]]
    actions, writes = [], False
    for _ in range(MAX_ROUNDS):
        reply = call_model(messages)
        calls = reply.get('tool_calls') or []
        if not calls:
            return {'reply': (reply.get('content') or '').strip(), 'actions': actions, 'changed': writes}
        messages.append({'role': 'assistant', 'content': reply.get('content') or '', 'tool_calls': calls})
        for call in calls:
            name = call['function']['name']
            outcome, args = run_tool(name, call['function'].get('arguments'))
            action = describe(name, args, outcome)
            if action:
                actions.append(action)
                writes = True
            text = json.dumps(outcome, ensure_ascii=False)
            messages.append({'role': 'tool', 'tool_call_id': call['id'], 'content': text[:12000]})
    return {'reply': 'وقفت قبل ما أخلص لأن الطلب محتاج خطوات كتير. قسّمه على رسالتين.', 'actions': actions, 'changed': writes}


@router.get('/api/assistant/status', dependencies=[Depends(core.authorized)])
def assistant_status():
    options = providers()
    mine = own_key()
    return {'configured': bool(options), 'model': options[0][2] if options else '',
            'fallback': options[1][2] if len(options) > 1 else '', 'own': bool(mine)}


class OwnKey(BaseModel):
    provider: str = Field(pattern='^(groq|gemini|openrouter)$')
    key: str = Field(min_length=10, max_length=300)


def list_models(base, key):
    request = urllib.request.Request(base + '/models', headers={'Authorization': 'Bearer ' + key, 'User-Agent': 'StudyHub/1.0'})
    with urllib.request.urlopen(request, timeout=20) as response:
        data = json.loads(response.read())
    return [m.get('id', '') for m in data.get('data', []) if m.get('id')]


@router.get('/api/assistant/key', dependencies=[Depends(core.authorized)])
def get_own_key():
    saved = core.setting('own_llm', {})
    return {'set': bool(own_key()), 'provider': saved.get('provider', ''), 'model': saved.get('model', ''),
            'last4': saved.get('last4', ''), 'providers': {k: {'name': v['name'], 'keys_url': v['keys_url'], 'prefix': v['prefix']}
                                                           for k, v in USER_PROVIDERS.items()}}


@router.post('/api/assistant/key', dependencies=[Depends(core.authorized)])
def set_own_key(body: OwnKey):
    """Save the user's own API key (encrypted) after checking it really works."""
    from . import accounts
    from .keystore import seal
    user = accounts.CURRENT_USER.get()
    accounts.throttle('llm-key:' + (user['id'] if user else 'owner'), 10, 3600)
    key = body.key.strip()
    if any(c.isspace() for c in key):
        raise HTTPException(400, 'الـ key فيه مسافات. انسخه تاني من غير مسافات')
    info = USER_PROVIDERS[body.provider]
    try:
        models = list_models(provider_base(body.provider), key)
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise HTTPException(400, f'الـ key ده مش شغال مع {info["name"]}. اتأكد إنك نسخته كله وإنك اخترت المزوّد الصح')
        raise HTTPException(502, f'{info["name"]} مردّش دلوقتي ({error.code}). جرّب كمان شوية')
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise HTTPException(502, f'مش قادر أوصل لـ {info["name"]} من السيرفر دلوقتي')
    model = info['model'] if info['model'] in models else next((m for m in models if 'gpt-oss-120b' in m), models[0] if models else info['model'])
    core.set_setting('own_llm', {'provider': body.provider, 'key': seal(key), 'model': model, 'last4': key[-4:]})
    return {'ok': True, 'provider': body.provider, 'model': model, 'last4': key[-4:]}


@router.delete('/api/assistant/key', dependencies=[Depends(core.authorized)])
def delete_own_key():
    core.set_setting('own_llm', {})
    return {'ok': True}
