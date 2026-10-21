"""Request guard (pure ASGI): per-client rate limiting and a body-size cap for non-upload routes.

- Rate limit: STUDY_RATE_LIMIT requests per minute per client (default 300) on /api and /mcp,
  twice that for static files. The client is the address the local proxy appended last to
  X-Forwarded-For (or the session cookie when no address is known), so faking the header
  does not help and one noisy client cannot slow the site down for everyone else.
- Body cap: STUDY_MAX_BODY_MB (default 4) for every route except the streaming upload endpoints,
  which are authenticated first and enforce their own per-user quotas.
"""
import hashlib
import json
import os
import threading
import time
from collections import deque

UPLOAD_PREFIXES = ('/api/uploads', '/api/vault/upload-zip', '/api/vault/upload-file', '/api/voices/')
LOOPBACK = {'127.0.0.1', '::1', 'localhost'}


def _header(scope, name):
    for key, value in scope.get('headers', []):
        if key == name:
            return value.decode('latin-1')
    return ''


def client_key(scope):
    host = (scope.get('client') or ('unknown', 0))[0]
    if host in LOOPBACK:
        hops = [h.strip() for h in _header(scope, b'x-forwarded-for').split(',') if h.strip()]
        if hops:
            return hops[-1][:64]
        cookie = _header(scope, b'cookie')
        if 'study_session=' in cookie:
            return 'session:' + hashlib.sha256(cookie.split('study_session=', 1)[1].split(';', 1)[0].encode()).hexdigest()[:16]
    return host


async def _reply(send, status, message, extra=()):
    body = json.dumps({'detail': message}, ensure_ascii=False).encode()
    await send({'type': 'http.response.start', 'status': status, 'headers': [
        (b'content-type', b'application/json; charset=utf-8'), (b'content-length', str(len(body)).encode()),
        (b'x-content-type-options', b'nosniff'), *extra]})
    await send({'type': 'http.response.body', 'body': body})


class TooLarge(Exception):
    pass


class RequestGuard:
    def __init__(self, app):
        self.app = app
        self.hits = {}
        self.lock = threading.Lock()
        self.last_sweep = time.monotonic()

    def allowed(self, key, limit):
        now = time.monotonic()
        with self.lock:
            if now - self.last_sweep > 120:  # forget idle clients
                self.hits = {k: q for k, q in self.hits.items() if q and q[-1] > now - 60}
                self.last_sweep = now
            q = self.hits.setdefault(key, deque())
            while q and q[0] <= now - 60:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        path = scope.get('path', '')
        limit = int(os.environ.get('STUDY_RATE_LIMIT', '300'))
        if not path.startswith(('/api/', '/mcp')):
            limit *= 2
        if path != '/health' and not self.allowed(client_key(scope) + ('' if path.startswith(('/api/', '/mcp')) else ':static'), limit):
            return await _reply(send, 429, 'طلبات كتير جدًا. استنى دقيقة وجرّب تاني', [(b'retry-after', b'60')])
        if path.startswith(UPLOAD_PREFIXES) and scope.get('method') == 'POST':
            return await self.app(scope, receive, send)
        cap = int(float(os.environ.get('STUDY_MAX_BODY_MB', '4')) * 1024 * 1024)
        declared = _header(scope, b'content-length')
        if declared.isdigit() and int(declared) > cap:
            return await _reply(send, 413, 'الطلب كبير جدًا')
        seen = 0
        started = False

        async def limited_receive():
            nonlocal seen
            message = await receive()
            if message['type'] == 'http.request':
                seen += len(message.get('body', b''))
                if seen > cap:
                    raise TooLarge()
            return message

        async def tracked_send(message):
            nonlocal started
            if message['type'] == 'http.response.start':
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except TooLarge:
            if not started:
                await _reply(send, 413, 'الطلب كبير جدًا')
