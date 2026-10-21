"""python -m studyhub init|serve|seed-vault|password|token|llm."""
import argparse
import getpass
import hashlib
import json
import os
import secrets
import sqlite3
from contextlib import closing
from pathlib import Path


def load_config():
    config_path = Path(os.environ.get('STUDY_CONFIG_FILE', 'data/studyhub/config.json'))
    if config_path.is_file():
        for key, value in json.loads(config_path.read_text()).items():
            os.environ.setdefault(key, str(value))
    return config_path


def password_hash(password):
    salt = secrets.token_bytes(16)
    return salt.hex() + ':' + hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 600_000).hex()


def seed_vault():
    import uuid
    import time
    from .app import mounts, vault_path, rows, put
    added = 0
    existing = {c.get('vault_folder') for c in rows('courses')}
    for mount in mounts():
        base = vault_path(mount)
        if not base.is_dir():
            continue
        for path in sorted(base.iterdir()):
            if not path.is_dir() or path.name.startswith('.') or path.is_symlink():
                continue
            folder = mount + '/' + path.name
            if folder in existing:
                continue
            put('courses', {'id': uuid.uuid4().hex, 'title': path.name, 'kind': 'college',
                            'vault_folder': folder, 'created': time.time()})
            existing.add(folder)
            added += 1
    print(f'Added {added} courses from the selected vault folders.')


def available_models(base, key):
    """Ask the provider which model ids this key can use (OpenAI-compatible GET /models)."""
    import urllib.request
    request = urllib.request.Request(base.rstrip('/') + '/models', headers={'Authorization': 'Bearer ' + key, 'User-Agent': 'StudyHub/1.0'})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read())
        return sorted(m['id'].removeprefix('models/') for m in data.get('data', []))
    except Exception as error:  # network/key problems are reported, not fatal
        print(f'  (could not list models: {error})')
        return []


def pick_model(base, key, model):
    models = available_models(base, key)
    if models and model not in models:
        chat = [m for m in models if not any(x in m for x in ('whisper', 'tts', 'embed', 'guard', 'image', 'audio', 'veo', 'imagen'))]
        print(f'  "{model}" is not available for this key. Models you can use:')
        for m in chat[:40]:
            print('   -', m)
        model = input('  Model to use: ').strip() or (chat[0] if chat else model)
    elif models:
        print(f'  OK: {model} is available.')
    return model


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['init', 'serve', 'seed-vault', 'password', 'token', 'llm', 'users'])
    parser.add_argument('--vault-root', type=Path)
    parser.add_argument('--archive-root', type=Path)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8091)
    parser.add_argument('rest', nargs='*', help='users: list | disable NAME | enable NAME | college NAME | no-college NAME | delete NAME | reset-password NAME')
    args = parser.parse_args()
    config_path = load_config()
    if args.command in ('init', 'password'):
        config_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        config = json.loads(config_path.read_text()) if config_path.is_file() else {}
        password = getpass.getpass('New StudyHub password (at least 12 characters): ')
        if len(password) < 12:
            parser.error('Use at least 12 characters.')
        if password != getpass.getpass('Confirm password: '):
            parser.error('Passwords do not match.')
        config['STUDY_PASSWORD_HASH'] = password_hash(password)
        config['STUDY_DATA_DIR'] = str(config_path.parent.resolve())
        if args.vault_root:
            config['STUDY_VAULT_ROOT'] = str(args.vault_root.resolve())
        if args.archive_root:
            config['ARCHIVE_DATA_DIR'] = str(args.archive_root.resolve())
        fd = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(json.dumps(config, indent=2))
        os.chmod(config_path, 0o600)
        db_path = config_path.parent / 'study.sqlite3'
        if db_path.exists():
            with closing(sqlite3.connect(db_path)) as db, db:
                db.execute('DELETE FROM sessions')
        print('Private configuration saved. Password was not stored in plain text.')
        return
    if args.command == 'users':
        # Manage accounts of the multi-user site (STUDY_MULTIUSER=1).
        import shutil
        import time as _time
        from . import accounts
        action, name = (args.rest + ['list', ''])[:2] if args.rest else ('list', '')
        with accounts.accounts_db() as db:
            if action == 'list':
                for r in db.execute('SELECT * FROM users ORDER BY created'):
                    seen = _time.strftime('%Y-%m-%d %H:%M', _time.localtime(r['last_seen'])) if r['last_seen'] else 'never'
                    size = accounts.folder_size(accounts.base_root() / 'users' / r['id']) / 1048576
                    college = 'FCI' if r['college'] else '-'
                    print(f"{r['username']:<20} {r['name']:<24} {'DISABLED' if r['disabled'] else 'active':<9} {college:<4} {size:7.1f} MB  last seen {seen}")
                return
            row = db.execute('SELECT * FROM users WHERE username=?', (name.lower(),)).fetchone()
            if not row:
                parser.error(f'No user named {name!r}.')
            if action in ('disable', 'enable'):
                db.execute('UPDATE users SET disabled=? WHERE id=?', (1 if action == 'disable' else 0, row['id']))
                db.execute('DELETE FROM sessions WHERE user_id=?', (row['id'],))
            elif action in ('college', 'no-college'):
                # FCI students see the shared college archive; others don't.
                db.execute('UPDATE users SET college=? WHERE id=?', (1 if action == 'college' else 0, row['id']))
            elif action == 'reset-password':
                password = getpass.getpass(f'New password for {name}: ')
                if len(password) < 8:
                    parser.error('Use at least 8 characters.')
                db.execute('UPDATE users SET password=? WHERE id=?', (accounts.hash_password(password), row['id']))
                db.execute('DELETE FROM sessions WHERE user_id=?', (row['id'],))
            elif action == 'delete':
                if input(f'Delete {name} and ALL their data? Type the username to confirm: ').strip().lower() != name.lower():
                    parser.error('Not confirmed.')
                for table in ('sessions', 'api_tokens'):
                    db.execute(f'DELETE FROM {table} WHERE user_id=?', (row['id'],))
                db.execute('DELETE FROM users WHERE id=?', (row['id'],))
                shutil.rmtree(accounts.base_root() / 'users' / row['id'], ignore_errors=True)
            else:
                parser.error('users: list | disable NAME | enable NAME | college NAME | no-college NAME | delete NAME | reset-password NAME')
        print(f'{action}: {name} done.')
        return
    if args.command == 'llm':
        # Model for the in-app assistant (any OpenAI-compatible API with tool calling).
        providers = {'1': ('Groq (free)', 'https://api.groq.com/openai/v1', 'llama-3.3-70b-versatile'),
                     '2': ('Google Gemini (free tier)', 'https://generativelanguage.googleapis.com/v1beta/openai', 'gemini-3.8-flash'),
                     '3': ('OpenRouter', 'https://openrouter.ai/api/v1', 'meta-llama/llama-3.3-70b-instruct:free'),
                     '4': ('Ollama on this server', 'http://127.0.0.1:11434/v1', 'qwen2.5:7b')}
        for number, (label, _, model) in providers.items():
            print(f'  {number}) {label}  [{model}]')
        choice = input('Provider [1]: ').strip() or '1'
        if choice not in providers:
            parser.error('Pick 1-4.')
        label, base, model = providers[choice]
        model = input(f'Model [{model}]: ').strip() or model
        key = '' if choice == '4' else getpass.getpass('API key (hidden): ').strip()
        model = pick_model(base, key, model)
        config = json.loads(config_path.read_text()) if config_path.is_file() else {}
        config.update({'STUDY_LLM_BASE_URL': base, 'STUDY_LLM_MODEL': model, 'STUDY_LLM_API_KEY': key})
        for name in ('STUDY_LLM_FALLBACK_BASE_URL', 'STUDY_LLM_FALLBACK_MODEL', 'STUDY_LLM_FALLBACK_API_KEY'):
            config.pop(name, None)
        if input('Add a fallback provider for when the first one hits its limit? [y/N]: ').strip().lower() == 'y':
            for number, (label2, _, model2) in providers.items():
                if number != choice:
                    print(f'  {number}) {label2}  [{model2}]')
            second = input('Fallback provider [2]: ').strip() or '2'
            if second in providers and second != choice:
                _, base2, model2 = providers[second]
                model2 = input(f'Model [{model2}]: ').strip() or model2
                key2 = '' if second == '4' else getpass.getpass('Fallback API key (hidden): ').strip()
                model2 = pick_model(base2, key2, model2)
                config.update({'STUDY_LLM_FALLBACK_BASE_URL': base2, 'STUDY_LLM_FALLBACK_MODEL': model2,
                               'STUDY_LLM_FALLBACK_API_KEY': key2})
        fd = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(json.dumps(config, indent=2))
        os.chmod(config_path, 0o600)
        print(f'Saved {label} / {model}. Restart the StudyHub service to apply (e.g. sudo systemctl restart studyhub)')
        return
    if args.command == 'token':
        # One token for the MCP connector (Claude) and Bearer API calls (n8n, scripts).
        # Running it again rotates the token and invalidates the old one after a restart.
        config = json.loads(config_path.read_text()) if config_path.is_file() else {}
        token = secrets.token_urlsafe(32)
        config['STUDY_API_TOKEN_HASH'] = hashlib.sha256(token.encode()).hexdigest()
        config_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(json.dumps(config, indent=2))
        os.chmod(config_path, 0o600)
        origin = os.environ.get('STUDY_PUBLIC_ORIGIN', 'https://<your-studyhub-domain>').rstrip('/')
        if os.environ.get('STUDY_TOKEN_URL_ONLY') == '1':  # used by deploy script: no token on screen
            print(origin + '/mcp/' + token)
            return
        print('New API token (shown once, store it in a password manager):\n')
        print('  ' + token + '\n')
        print('Claude custom connector URL:\n  ' + origin + '/mcp/' + token + '\n')
        print('Header-based clients (Claude Code, n8n, curl):\n  Authorization: Bearer ' + token + '\n')
        print('Restart the StudyHub service to apply (e.g. sudo systemctl restart studyhub)')
        return
    if args.command == 'seed-vault':
        seed_vault()
        return
    if not os.environ.get('STUDY_PASSWORD_HASH') and os.environ.get('STUDY_MULTIUSER') != '1':
        parser.error('Run python -m studyhub init first.')
    import uvicorn
    uvicorn.run('studyhub.app:app', host=args.host, port=args.port, proxy_headers=False)


if __name__ == '__main__':
    main()
