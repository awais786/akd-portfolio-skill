"""Owns the one AKD daemon for this process: build it, log in once, supervise it."""
from __future__ import annotations

import contextlib

import httpx

from . import config, credentials
from .daemon import AkdDaemon
from .store import SnapshotStore
from .supervisor import DaemonSupervisor


class LoginRejected(Exception):
    """AKD looked at the credentials and said no."""


class AkdUnreachable(Exception):
    """We could not complete the SOAP call at all (network, DNS, timeout)."""


class AkdManager:
    def __init__(self, store: SnapshotStore) -> None:
        self.store = store
        self._http: httpx.AsyncClient | None = None
        self._daemon: AkdDaemon | None = None
        self._supervisor: DaemonSupervisor | None = None

    def _ensure(self) -> None:
        """Construct the daemon WITHOUT starting its loop. `connect()` must make
        exactly one login attempt before anything else logs in: two attempts per
        click is the wrong thing to do to a broker that locks accounts."""
        if self._daemon is None:
            self._http = httpx.AsyncClient(timeout=30.0)
            self._daemon = AkdDaemon(account=config.ACCOUNT_REF, store=self.store, http=self._http)
            self._supervisor = DaemonSupervisor("akd", self._daemon.run)

    async def start_persisted(self) -> bool:
        """At app startup: if encrypted credentials are on disk, restore and start."""
        if not credentials.load_persisted():
            return False
        self._ensure()
        await self._supervisor.start()
        return True

    async def connect(self, user_id: str, password: str) -> dict:
        credentials.set_credentials(user_id, password)
        self._ensure()
        try:
            parsed = await self._daemon.login()
        except Exception as exc:
            credentials.clear_credentials()
            raise AkdUnreachable(f"could not reach AKD: {exc}") from exc
        if not parsed.get("valid"):
            credentials.clear_credentials()
            raw = (parsed.get("raw") or "").strip()
            full = (parsed.get("response_body") or "").strip()
            status = parsed.get("http_status")
            if raw:
                detail = raw[:200]
            elif full:
                detail = f"HTTP {status}, empty <return>; body: {full[:300]}"
            else:
                detail = f"HTTP {status}, completely empty response - account is likely locked/rate-limited"
            raise LoginRejected(f"AKD login rejected - {detail}")
        await self._supervisor.start()
        return {"connected": True, "account": user_id.strip()}

    async def disconnect(self) -> None:
        credentials.clear_credentials()
        await self.stop()

    async def stop(self) -> None:
        if self._supervisor is not None:
            with contextlib.suppress(Exception):
                await self._supervisor.stop()
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        self._daemon = None
        self._supervisor = None

    def daemon_status(self) -> dict:
        state = self._supervisor.status() if self._supervisor else {"name": "akd", "state": "stopped", "error": None}
        if self._daemon is not None:
            detail = self._daemon.status()
            # the supervisor's error covers crashes; the daemon's covers rejected logins/poll failures
            state = {**state, "authenticated": detail["authenticated"], "last_error": detail["last_error"]}
            state["error"] = state["error"] or detail["last_error"]
        return state
