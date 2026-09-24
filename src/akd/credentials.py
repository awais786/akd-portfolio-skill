"""AKD login credentials: memory first, encrypted on disk so a restart doesn't force a re-login.

The password is Fernet-encrypted with a key kept in a sibling 0600 file under
AKD_DATA_DIR. That protects against casual disclosure (backups, a copied
`akd_creds.enc`), NOT against someone who can read both files as you. Treat the
data directory as sensitive. `AKD_PASSWORD` is a headless fallback only. It is
never logged and never returned by any endpoint.
"""
from __future__ import annotations

import contextlib
import json
import os
from dataclasses import dataclass

from . import config


@dataclass
class _Creds:
    user_id: str  # the AKD account used as the SOAP UserName, e.g. COAF00000
    password: str


_creds: _Creds | None = None


def _cred_file():
    return config.DATA_DIR / "akd_creds.enc"


def _key_file():
    return config.DATA_DIR / "akd_creds.key"


def _fernet():
    from cryptography.fernet import Fernet

    config.ensure_data_dir()
    if _key_file().exists():
        key = _key_file().read_bytes()
    else:
        key = Fernet.generate_key()
        _key_file().write_bytes(key)
        with contextlib.suppress(OSError):
            _key_file().chmod(0o600)
    return Fernet(key)


def _persist() -> None:
    with contextlib.suppress(Exception):  # best-effort; the in-memory session still works
        if _creds is None:
            _cred_file().unlink(missing_ok=True)
            return
        token = _fernet().encrypt(json.dumps({"user_id": _creds.user_id, "password": _creds.password}).encode())
        _cred_file().write_bytes(token)
        _cred_file().chmod(0o600)


def load_persisted() -> bool:
    """Load encrypted credentials into memory. True when a session was restored."""
    global _creds
    try:
        if not _cred_file().exists():
            return False
        data = json.loads(_fernet().decrypt(_cred_file().read_bytes()).decode())
        _creds = _Creds(user_id=data["user_id"], password=data["password"])
        return True
    except Exception:  # noqa: BLE001 - any failure means "start fresh"
        return False


def set_credentials(user_id: str, password: str) -> None:
    global _creds
    _creds = _Creds(user_id=user_id.strip(), password=password)
    _persist()


def clear_credentials() -> None:
    global _creds
    _creds = None
    _persist()


def login_user_id(fallback: str) -> str:
    return _creds.user_id if _creds and _creds.user_id else fallback


def password() -> str | None:
    if _creds and _creds.password:
        return _creds.password
    return os.environ.get("AKD_PASSWORD")


def has_credentials() -> bool:
    return bool((_creds and _creds.password) or os.environ.get("AKD_PASSWORD"))
