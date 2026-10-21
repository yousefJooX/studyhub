"""Black-box security checks for StudyHub. Each check prints PASS/FAIL.

Run against a THROWAWAY local instance in friends mode (it creates accounts and junk data):

    STUDY_MULTIUSER=1 STUDY_DATA_DIR=/tmp/sh-test STUDY_SECURE_COOKIE=true STUDY_REMINDERS_ENABLED=false \
    STUDY_USER_QUOTA_MB=20 STUDY_MIN_FREE_MB=1 STUDY_PUBLIC_ORIGIN=https://example.test \
      python -m uvicorn studyhub.app:app --port 8096 &
    STUDY_TEST_DATA=/tmp/sh-test python studyhub/tests/security_check.py

Never point it at a real site.
"""
import io, json, re, time, zipfile, threading, concurrent.futures as cf
import requests

import os, pathlib
B = os.environ.get('STUDY_TEST_URL', 'http://127.0.0.1:8096')
ORIGIN = os.environ.get('STUDY_TEST_ORIGIN', 'https://example.test')
DATA = os.environ.get('STUDY_TEST_DATA', '/tmp/sh-test')
SRC = pathlib.Path(__file__).resolve().parents[1]
results = []

def check(name, ok, detail=''):
    results.append((name, bool(ok), detail))
    print(('PASS ' if ok else 'FAIL ') + name + (f'  [{detail}]' if detail else ''))

def cookie_of(r):
    m = re.search(r'study_session=([^;]+)', r.headers.get('set-cookie', ''))
    return m.group(1) if m else None

class U:
    def __init__(self, name, ip):
        self.ip = ip
        r = requests.post(B + '/api/signup', json={'name': name, 'username': name, 'password': 'Sup3r-secret-pass'},
                          headers={'X-Forwarded-For': ip})
        assert r.status_code == 200, r.text
        self.c = cookie_of(r); self.setcookie = r.headers.get('set-cookie', '')
    def h(self, extra=None):
        return {'Cookie': f'study_session={self.c}', 'X-Forwarded-For': self.ip, **(extra or {})}
    def get(self, p, **k): return requests.get(B + p, headers=self.h(k.pop('headers', None)), **k)
    def post(self, p, **k): return requests.post(B + p, headers=self.h(k.pop('headers', None)), **k)
    def put(self, p, **k): return requests.put(B + p, headers=self.h(k.pop('headers', None)), **k)
    def delete(self, p, **k): return requests.delete(B + p, headers=self.h(k.pop('headers', None)), **k)

alice, bob = U('alice', '10.0.0.1'), U('bob', '10.0.0.2')

# ---------- A01 Broken access control / auth bypass
routes = []
for f in SRC.glob('*.py'):
    for m in re.finditer(r"^@(app|router|account)\.(get|post|put|delete|patch)\('([^']*)'", f.read_text(), re.M):
        routes.append(list(m.groups()))
prefix = {'app': '', 'router': None, 'account': '/api/account'}
paths = set()
for owner, method, path in routes:
    if owner == 'router' and not path.startswith(('/api', '/mcp')):
        for pre in ('/api/notifications/push', '/api/vault', '/api/voices'):
            paths.add((method.upper(), pre + path))
    elif owner == 'account':
        paths.add((method.upper(), '/api/account' + path))
    else:
        paths.add((method.upper(), path))
public = {'/api/auth', '/api/signup', '/api/login', '/health', '/', '/sw.js', '/manifest.webmanifest'}
leaks = []
for method, path in sorted(paths):
    url = re.sub(r'\{[^}]+\}', 'x', path)
    if path in public or url.endswith('/x') and path.startswith('/mcp'):
        continue
    r = requests.request(method, B + url, json={}, headers={'Origin': ORIGIN})
    if r.status_code < 400 or r.status_code >= 500:
        leaks.append(f'{method} {url} -> {r.status_code}')
check('Every private endpoint refuses requests without login', not leaks, '; '.join(leaks[:5]) or f'{len(paths)} routes tried')

tricks = {
    'forged cookie': {'Cookie': 'study_session=' + 'A' * 43},
    'empty bearer': {'Authorization': 'Bearer '},
    'bearer null': {'Authorization': 'Bearer null'},
    'bearer undefined': {'Authorization': 'Bearer undefined'},
    'SQL in cookie': {'Cookie': "study_session=' OR '1'='1"},
    'X-Forwarded-User': {'X-Forwarded-User': 'alice', 'X-Remote-User': 'alice'},
}
bad = [k for k, h in tricks.items() if requests.get(B + '/api/state', headers=h).status_code == 200]
check('Forged cookies / tokens / proxy auth headers do not log you in', not bad, ', '.join(bad))
odd = ['//api/state', '/API/state', '/api/state/', '/api//state', '/static/../api/state', '/api/state%00', '/api/%73tate']
bad = [p for p in odd if requests.get(B + p).status_code == 200 and 'courses' in requests.get(B + p).text]
check('URL tricks (case, double slash, ../, %00, encoding) do not bypass login', not bad, ', '.join(bad))
bad = [m for m in ('HEAD', 'OPTIONS', 'TRACE', 'PATCH') if requests.request(m, B + '/api/state').status_code == 200]
check('Other HTTP methods do not bypass login', not bad, ', '.join(bad))
r = requests.post(B + '/mcp/../api/state', json={}); r2 = requests.post(B + '/mcp/' + 'x' * 43, json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
check('MCP endpoint rejects wrong tokens', r2.status_code == 401, f'{r2.status_code}')

# login bypass via SQL injection / type confusion
payloads = ["' OR 1=1--", "alice' --", 'alice" OR "1"="1', "admin'/*", {'$ne': None}, ['alice'], None, 0, True]
ok = True
for p in payloads:
    r = requests.post(B + '/api/login', json={'username': p, 'password': p if isinstance(p, str) else "' OR 1=1--"},
                      headers={'X-Forwarded-For': f'10.9.{payloads.index(p)}.1'})
    if r.status_code == 200 or r.status_code >= 500:
        ok = False
check('SQL injection / type confusion in the login form fails', ok)

# IDOR: bob touches alice's data
t = alice.post('/api/tasks', json={'title': 'alice secret task'}).json()
n = alice.post('/api/notes', json={'title': 'alice secret', 'content': 'secret'}).json()
c = alice.post('/api/courses', json={'title': 'Alice course'}).json()
tid, nid, cid = t.get('id'), n.get('id'), c.get('id')
up = alice.post('/api/uploads?course_id=&name=secret.pdf', data=b'%PDF-1.4 secret', headers={'Content-Type': 'application/pdf'})
uid = up.json().get('id') if up.ok else None
idor = []
for m, p, body in [('GET', f'/api/tasks/{tid}', None), ('PUT', f'/api/tasks/{tid}', {'title': 'pwned'}), ('DELETE', f'/api/tasks/{tid}', None),
                   ('PUT', f'/api/notes/{nid}', {'title': 'pwned'}), ('DELETE', f'/api/notes/{nid}', None),
                   ('PUT', f'/api/courses/{cid}', {'title': 'pwned'}), ('DELETE', f'/api/courses/{cid}', None),
                   ('GET', f'/api/uploads/{uid}', None), ('DELETE', f'/api/uploads/{uid}', None)]:
    r = requests.request(m, B + p, json=body, headers={**bob.h(), 'Origin': ORIGIN})
    if r.status_code < 400:
        idor.append(f'{m} {p} -> {r.status_code}')
st = bob.get('/api/state').json()
leak = 'alice secret' in json.dumps(st, ensure_ascii=False) or 'Alice course' in json.dumps(st, ensure_ascii=False)
alive = alice.get('/api/state').json()
intact = any(x['title'] == 'alice secret task' for x in alive['tasks'])
check("A user cannot read, edit or delete another user's tasks/notes/courses/files (IDOR)", not idor and not leak and intact, '; '.join(idor))

# ---------- A03 Injection
sqli = ["'; DROP TABLE tasks;--", "1' OR '1'='1", "\" OR \"\"=\"", "%' UNION SELECT data FROM sessions--"]
errs = []
for s in sqli:
    for p in [f'/api/search?q={s}', f'/api/vault/files?folder={s}', f'/api/tasks/{s}']:
        r = alice.get(p)
        if r.status_code >= 500:
            errs.append(p)
    r = alice.post('/api/tasks', json={'title': s, 'course_id': s})
    if r.status_code >= 500:
        errs.append('create task')
still = len(alice.get('/api/state').json()['tasks']) >= 1
check('SQL injection in search, paths and form fields does nothing', not errs and still, ', '.join(errs[:4]))

xss = '<script>alert(1)</script><img src=x onerror=alert(1)>'
alice.post('/api/notes', json={'title': xss, 'content': f'# hi\n{xss}\n[click](javascript:alert(1))\n<iframe src=//evil.com>'})
html_out = alice.get('/api/notes').text if alice.get('/api/notes').status_code == 200 else ''
alice.post('/api/vault/upload-file?path=' + requests.utils.quote('Notes/x.md'), data=f'# t\n{xss}\n[c](javascript:alert(1))\n<svg onload=alert(1)>'.encode())
nd = alice.get('/api/vault/note-data?path=Notes/x.md')
rendered = nd.json().get('html', '') if nd.ok else ''
tags = re.findall(r'<\s*(script|img|svg|iframe)\b', rendered, re.I) + re.findall(r'href\s*=\s*["\']?\s*javascript:', rendered, re.I) + re.findall(r'<[^>]+\son\w+=', rendered, re.I)
check('Markdown notes cannot inject scripts (XSS)', nd.ok and not tags, f'note-data {nd.status_code} {tags}')
page = alice.get('/api/vault/note?path=Notes/x.md')
check('Standalone note page escapes content and has a strict CSP', page.ok and '<script>alert' not in page.text and "script-src 'self'" in page.headers.get('content-security-policy', ''))

# path traversal
trav = ['../../accounts.sqlite3', '..%2f..%2faccounts.sqlite3', '/etc/passwd', 'Notes/../../../accounts.sqlite3', '....//....//etc/passwd', 'Notes/%2e%2e/%2e%2e/accounts.sqlite3']
bad = []
for p in trav:
    for ep in ('/api/vault/note?path=', '/api/vault/note-data?path=', '/api/vault/file?path=', '/api/vault/raw?path='):
        r = alice.get(ep + p)
        if r.status_code == 200 and ('SQLite' in r.text or 'root:' in r.text):
            bad.append(ep + p)
check('Path traversal (../, /etc/passwd, encoded) cannot read server files', not bad, ', '.join(bad[:3]))

# ---------- Malicious uploads
evil = {'evil.html': (b'<html><script>fetch("/api/state")</script></html>', 'text/html'),
        'evil.svg': (b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>', 'image/svg+xml'),
        'evil.js': (b'alert(1)', 'application/javascript'),
        'shell.php': (b'<?php system($_GET[c]); ?>', 'application/x-php'),
        'evil.py': (b'import os; os.system("id")', 'text/x-python'),
        '../../../evil.pdf': (b'%PDF-1.4', 'application/pdf'),
        'fake.pdf': (b'<html><script>alert(1)</script></html>', 'application/pdf')}
issues = []
for name, (data, ctype) in evil.items():
    r = alice.post('/api/uploads?course_id=&name=' + requests.utils.quote(name), data=data, headers={'Content-Type': ctype})
    if not r.ok:
        continue
    e = r.json()
    if '..' in json.dumps(e) and '/' in e.get('name', ''):
        issues.append('traversal name kept: ' + e.get('name', ''))
    g = alice.get(f"/api/uploads/{e['id']}")
    disp = g.headers.get('content-disposition', ''); ct = g.headers.get('content-type', '')
    if name.endswith(('.html', '.svg', '.js', '.php', '.py')) and 'attachment' not in disp:
        issues.append(f'{name} served inline as {ct}')
    if 'nosniff' not in g.headers.get('x-content-type-options', ''):
        issues.append(f'{name} without nosniff')
    if "script-src 'self'" not in g.headers.get('content-security-policy', ''):
        issues.append(f'{name} without CSP')
check('Uploaded HTML/SVG/JS/PHP/Python files are downloaded, never run or rendered', not issues, '; '.join(issues[:4]))
import glob, os
outside = [p for p in glob.glob(DATA + '/**/evil*', recursive=True) if '/uploads/' not in p and '/users/' not in p]
check('Upload file names cannot escape the user folder', not outside and not any('..' in i for i in issues), ', '.join(outside))

def zipbytes(entries, symlink=None):
    b = io.BytesIO()
    with zipfile.ZipFile(b, 'w') as z:
        for n, d in entries.items():
            z.writestr(n, d)
        if symlink:
            info = zipfile.ZipInfo(symlink[0]); info.external_attr = (0o120777 << 16); z.writestr(info, symlink[1])
    return b.getvalue()
z = zipbytes({'V/ok.md': '# ok', 'V/../../../../tmp/zipslip.md': 'x', '/abs/evil.md': 'x', 'V/.obsidian/workspace.json': '{}', 'V/run.sh': 'rm -rf /'},
             symlink=('V/link.md', '/etc/passwd'))
r = alice.post('/api/vault/upload-zip?name=V.zip', data=z)
files = [p for p in glob.glob(DATA + '/users/*/vault/**', recursive=True)]
slipped = os.path.exists('/tmp/zipslip.md') or any(p.endswith(('run.sh', 'workspace.json')) for p in files) or any(os.path.islink(p) for p in files)
check('ZIP upload blocks zip-slip, absolute paths, symlinks and scripts', r.ok and not slipped, f'{r.status_code} {r.text[:80]}')
bomb = io.BytesIO()
with zipfile.ZipFile(bomb, 'w', zipfile.ZIP_DEFLATED) as zf:
    zf.writestr('B/big.md', b'0' * (60 * 1024 * 1024))
r = alice.post('/api/vault/upload-zip?name=B.zip', data=bomb.getvalue())
check('ZIP bomb (60 MB of zeros in a tiny zip) is refused by the quota', r.status_code in (400, 413), f'{r.status_code}, zip {len(bomb.getvalue())//1024} KB')
r = alice.post('/api/voices/day?name=evil.html', data=b'<script>')
r2 = alice.post('/api/voices/day?name=x.mp3', data=b'<html><script>alert(1)</script>')
g = alice.get(r2.json()['url']) if r2.ok else None
check('Voice uploads accept audio names only and are served as audio', r.status_code == 400 and (g is None or g.headers.get('content-type', '').startswith('audio/')))
r = alice.post('/api/uploads?course_id=&name=big.pdf', data=b'0' * (25 * 1024 * 1024), headers={'Content-Type': 'application/pdf'})
check('Uploads over the per-user quota are refused', r.status_code == 413, f'{r.status_code}')

# ---------- CSRF / clickjacking / headers
r = requests.post(B + '/api/tasks', json={'title': 'csrf'}, headers={**alice.h(), 'Origin': 'https://evil.example'})
check('Requests from another website are rejected (CSRF)', r.status_code == 403, f'{r.status_code}')
sc = alice.setcookie.lower()
check('Session cookie is HttpOnly, Secure and SameSite=Strict', all(x in sc for x in ('httponly', 'secure', 'samesite=strict')), sc[:120])
h = requests.get(B + '/').headers
need = {'content-security-policy': "frame-ancestors 'none'", 'x-frame-options': 'DENY', 'x-content-type-options': 'nosniff',
        'strict-transport-security': 'max-age', 'referrer-policy': 'same-origin'}
miss = [k for k, v in need.items() if v.lower() not in h.get(k, '').lower()]
check('Security headers present (CSP, clickjacking, HSTS, nosniff, referrer)', not miss, ', '.join(miss))
check('No API docs / OpenAPI schema exposed', all(requests.get(B + p).status_code == 404 or 'openapi' not in requests.get(B + p).text for p in ('/docs', '/redoc', '/openapi.json')))
r = requests.get(B + '/api/tasks/' + "'")
check('Errors do not leak stack traces', 'Traceback' not in r.text and 'File "' not in r.text)

# ---------- Brute force
codes = [requests.post(B + '/api/login', json={'username': 'alice', 'password': f'wrong{i}'}, headers={'X-Forwarded-For': f'10.1.1.{i}'}).status_code for i in range(14)]
check('Password guessing is blocked after 10 tries (even from many IPs)', 429 in codes and codes.index(429) <= 10, str(codes))
codes = [requests.post(B + '/api/login', json={'username': f'user{i}', 'password': 'x'}, headers={'X-Forwarded-For': f'1.2.3.{i}, 10.7.7.7'}).status_code for i in range(18)]
check('Faking X-Forwarded-For does not get around the per-IP login limit', 429 in codes, str(codes))
codes = [requests.post(B + '/api/signup', json={'name': 'x', 'username': f'spam{i}', 'password': 'Sup3r-secret-pass'}, headers={'X-Forwarded-For': f'5.5.{i // 250}.{i % 250}'}).status_code for i in range(110)]
check('Mass sign-up bots are stopped by the hourly cap', 429 in codes, f'{codes.count(200)} accounts created before the cap')

# ---------- DoS
t0 = time.time()
try:
    r = requests.post(B + '/api/login', data=b'{"username":"' + b'a' * (200 * 1024 * 1024) + b'"}', headers={'Content-Type': 'application/json'}, timeout=30)
    code = r.status_code
except Exception as e:
    code = type(e).__name__
check('A 200 MB request to a public endpoint is rejected quickly', code in (413, 'ConnectionError') and time.time() - t0 < 10, f'{code} in {time.time()-t0:.1f}s')
r = requests.post(B + '/api/login', data=b'{' * 100000, headers={'Content-Type': 'application/json'})
check('Malformed / deeply nested JSON does not crash the server', r.status_code < 500, f'{r.status_code}')
r = alice.post('/api/tasks', json={'title': 'x' * 100000})
check('Over-long fields are refused', r.status_code == 422, f'{r.status_code}')

def hit(_):
    try:
        return requests.get(B + '/api/auth', timeout=10, headers={'X-Forwarded-For': '66.66.66.66'}).status_code
    except Exception:
        return 0
t0 = time.time()
with cf.ThreadPoolExecutor(50) as ex:
    flood = list(ex.map(hit, range(1500)))
dur = time.time() - t0
limited = flood.count(429)
ok_other = requests.get(B + '/api/state', headers=bob.h(), timeout=10).status_code == 200
check('A request flood from one address gets rate-limited and others still work', limited > 0 and ok_other, f'{limited}/1500 got 429 in {dur:.1f}s; other user ok={ok_other}')
check('Server survived all tests', requests.get(B + '/health', timeout=5).ok)

print('\nSUMMARY', sum(r[1] for r in results), '/', len(results), 'passed')
