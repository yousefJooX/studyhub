"""Multi-user site: each user uploads their own Obsidian folders (a ZIP, or a folder picked in the
browser file by file). Files land in <user>/vault/<folder>/ and the folder is added to their library."""
import os
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, Depends, HTTPException, Request

from . import accounts
from .app import DOCUMENTS, authorized, data_root, mounts, set_setting, vault_root

router = APIRouter(prefix='/api/vault', dependencies=[Depends(authorized)])
MAX_ZIP = 400 * 1024 * 1024
MAX_FILE = 100 * 1024 * 1024
MAX_FILES = 5000
SKIP_PARTS = {'.obsidian', '.trash', '.git', 'node_modules', '__pycache__', '.DS_Store'}


def require_multiuser():
    if not accounts.enabled():
        raise HTTPException(403, 'رفع الفولدرات متاح في موقع الحسابات بس')


def clean_relative(raw):
    """Normalise an uploaded path to safe parts, or None when it must be skipped."""
    parts = [p for p in PurePosixPath(raw.replace('\\', '/')).parts if p not in ('', '.', '/')]
    if not parts or any(p == '..' or p.startswith('.') or p in SKIP_PARTS for p in parts):
        return None
    if Path(parts[-1]).suffix.lower() not in DOCUMENTS:
        return None
    return parts


def add_mount(folder):
    current = mounts()
    if folder not in current:
        set_setting('mounts', current + [folder])


async def save_stream(request, target, limit):
    size = 0
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open('wb') as stream:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, 'الملف كبير على المساحة المتاحة')
                stream.write(chunk)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return size


@router.post('/upload-zip')
async def upload_zip(request: Request, name: str):
    """Upload an Obsidian folder as a .zip; it is extracted into a folder named after the zip."""
    require_multiuser()
    left = accounts.remaining_bytes()
    folder = Path(name.replace('\\', '/')).stem.strip()[:80]
    if not folder or folder.startswith('.') or '/' in folder:
        raise HTTPException(400, 'اسم الفولدر مش صالح')
    temp = Path(tempfile.mkstemp(dir=data_root(), suffix='.zip')[1])
    try:
        await save_stream(request, temp, min(MAX_ZIP, left))
        try:
            archive = zipfile.ZipFile(temp)
        except zipfile.BadZipFile:
            raise HTTPException(400, 'الملف مش ZIP سليم')
        members = [m for m in archive.infolist() if not m.is_dir()]
        names = [clean_relative(m.filename) for m in members]
        # A zip of "MyVault/..." should not become "MyVault/MyVault/..."
        tops = {n[0] for n in names if n}
        strip = len(tops) == 1 and all(n is None or len(n) > 1 for n in names)
        unpacked = sum(m.file_size for m, n in zip(members, names) if n)
        if unpacked > left:
            raise HTTPException(413, 'الفولدر بعد فك الضغط أكبر من المساحة المتاحة عندك')
        if sum(1 for n in names if n) > MAX_FILES:
            raise HTTPException(400, f'الفولدر فيه أكتر من {MAX_FILES} ملف')
        root = vault_root() / folder
        written = skipped = 0
        for member, parts in zip(members, names):
            if not parts or (member.external_attr >> 16) & 0o170000 == 0o120000:  # skip unsupported and symlinks
                skipped += 1
                continue
            parts = parts[1:] if strip else parts
            target = root.joinpath(*parts)
            if not target.resolve().is_relative_to(root.resolve()):
                skipped += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open('wb') as out:
                shutil.copyfileobj(source, out, 1024 * 1024)
            written += 1
        if not written:
            raise HTTPException(400, 'مفيش ملفات مدعومة جوه الـ ZIP (md، pdf، pptx، docx، صور…)')
        add_mount(folder)
        return {'folder': folder, 'files': written, 'skipped': skipped}
    finally:
        temp.unlink(missing_ok=True)


@router.post('/upload-file')
async def upload_file(request: Request, path: str):
    """One file of a folder picked in the browser; `path` keeps the folder structure (Folder/sub/x.md)."""
    require_multiuser()
    left = accounts.remaining_bytes()
    parts = clean_relative(path)
    if not parts or len(parts) < 2:
        return {'skipped': True}
    root = vault_root()
    target = root.joinpath(*parts)
    if not target.resolve().is_relative_to(root.resolve()):
        raise HTTPException(400, 'مسار غير صالح')
    await save_stream(request, target, min(MAX_FILE, left))
    add_mount(parts[0])
    return {'folder': parts[0], 'saved': '/'.join(parts)}


@router.delete('/folder')
def delete_folder(name: str):
    require_multiuser()
    if not name or '/' in name or name.startswith('.') or name == '..':
        raise HTTPException(400, 'اسم الفولدر مش صالح')
    target = vault_root() / name
    if target.is_dir() and not target.is_symlink():
        shutil.rmtree(target)
    set_setting('mounts', [m for m in mounts() if m != name and not m.startswith(name + '/')])
    return {'ok': True}


account = APIRouter(prefix='/api/account', dependencies=[Depends(authorized)])


def me():
    user = accounts.CURRENT_USER.get()
    if not user:
        raise HTTPException(403, 'متاح في موقع الحسابات بس')
    return user


@account.get('')
def account_info():
    user = me()
    root = accounts.user_root(user['id'])
    quota = int(os.environ.get('STUDY_USER_QUOTA_MB', '1024'))
    return {'user': accounts.public(user), 'token_created': accounts.has_api_token(user['id']),
            'used_mb': round(accounts.folder_size(root) / 1048576, 1), 'quota_mb': quota,
            'origin': os.environ.get('STUDY_PUBLIC_ORIGIN', '').rstrip('/')}


@account.post('/token')
def create_token():
    user = me()
    token = accounts.new_api_token(user['id'])
    origin = os.environ.get('STUDY_PUBLIC_ORIGIN', '').rstrip('/')
    return {'token': token, 'connector_url': f'{origin}/mcp/{token}', 'header_url': f'{origin}/mcp'}


@account.delete('/token')
def delete_token():
    accounts.revoke_api_token(me()['id'])
    return {'ok': True}


@account.post('/college')
def join_college(body: dict):
    user = me()
    accounts.throttle('college:' + user['id'], 10, 3600)
    if not accounts.college_code_ok(body.get('code', '')):
        raise HTTPException(403, 'كود الكلية غلط. اطلبه من جروب الدفعة')
    level, program = accounts.clean_profile(body.get('level'), body.get('program'))
    accounts.set_college(user['id'], True, level, program)
    return {'ok': True, 'college': True, 'level': level, 'program': program}


@account.post('/password')
def change_password(body: dict):
    user = me()
    old, new = str(body.get('old', '')), str(body.get('new', ''))
    if not accounts.check_password(old, user['password']):
        raise HTTPException(400, 'الباسورد الحالي غلط')
    if len(new) < 8:
        raise HTTPException(400, 'الباسورد الجديد لازم ٨ حروف على الأقل')
    with accounts.accounts_db() as db:
        db.execute('UPDATE users SET password=? WHERE id=?', (accounts.hash_password(new), user['id']))
        db.execute('DELETE FROM sessions WHERE user_id=?', (user['id'],))
    return {'ok': True, 'relogin': True}
