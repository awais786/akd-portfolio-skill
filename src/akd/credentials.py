"""AKD login credentials, held IN MEMORY ONLY.

The AKD password is never written to disk, in any form: not to a database, not to a file, not
encrypted. It lives in this process's memory for the length of one run.

  UserCredentials    an AKD login held in memory. What `portfolio.py` hands the daemon.
  EnvCredentials     fallback that reads AKD_PASSWORD (tests, scripts).

Passwords are never logged and never returned.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Creds:
    user_id: str   # the AKD account used as the SOAP UserName, e.g. COAF00000
    password: str


class UserCredentials:
    """An AKD login held in memory. Duck-typed provider the daemon reads: `password()` and
    `login_user_id(fallback)`."""

    def __init__(self) -> None:
        self._creds: Creds | None = None

    def set(self, akd_user_id: str, password: str) -> None:
        self._creds = Creds(user_id=akd_user_id.strip(), password=password)

    def login_user_id(self, fallback: str) -> str:
        return self._creds.user_id if self._creds and self._creds.user_id else fallback

    def password(self) -> str | None:
        return self._creds.password if self._creds and self._creds.password else None


class EnvCredentials:
    """Fallback: the password from AKD_PASSWORD, the account as given. Used by the daemon when no
    provider is injected (tests, scripts)."""

    def login_user_id(self, fallback: str) -> str:
        return fallback

    def password(self) -> str | None:
        return os.environ.get("AKD_PASSWORD")
