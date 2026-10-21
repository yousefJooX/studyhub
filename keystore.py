"""Encrypts per-user secrets (like a user's own AI API key) at rest.

The encryption key lives in <data>/secret.key (mode 600), separate from the users' databases,
so a copied study.sqlite3 alone does not reveal anyone's API key.
"""
import os

from cryptography.fernet import Fernet, InvalidToken


def _fernet():
    from .accounts import base_root
    path = base_root() / 'secret.key'
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as out:
            out.write(Fernet.generate_key())
    return Fernet(path.read_bytes().strip())


def seal(text):
    return _fernet().encrypt(text.encode()).decode()


def unseal(token):
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError, AttributeError):
        return ''
