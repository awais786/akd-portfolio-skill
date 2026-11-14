"""One-shot, headless portfolio fetch: log in to AKD once, read once, return JSON-able data.

Used by `python -m akd portfolio` and the `skills/akd-portfolio` skill. No server, no trade socket,
no polling loop. Rules that carry over from the app (see CLAUDE.md):

  * ONE login attempt. A rejected login raises `login_rejected` and nothing else is sent.
  * The AKD password stays in memory; it is never logged, returned, or put in an error message.
  * Absence is never emptiness: sections reuse the `reads.*` envelopes (`null` + `no_snapshot`).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import tempfile
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

import httpx

from . import reads
from .credentials import UserCredentials
from .daemon import AkdDaemon
from .store import SnapshotStore

__all__ = ["PortfolioError", "accounts_from_env", "fetch_portfolio", "run_portfolio"]

EXIT_CODES = {
    "missing_credentials": 2, "invalid_config": 2, "duplicate_account": 2,
    "login_rejected": 3, "akd_unreachable": 4,
}

_USER_VAR = re.compile(r"^AKD_USER_ID(?:_(\d+))?$")

_ACCOUNT_KEY = "portfolio"  # snapshot key inside the throwaway store; not an AKD identifier


class PortfolioError(Exception):
    """A fetch that cannot produce a portfolio. `code` is stable; `message` never has a secret."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


async def fetch_portfolio(user_id: str, password: str, *, http=None) -> dict:
    user_id = (user_id or "").strip()
    if not user_id or not (password or "").strip():
        raise PortfolioError(
            "missing_credentials", "AKD_USER_ID and AKD_PASSWORD must both be set and non-blank"
        )

    creds = UserCredentials()
    creds.set(user_id, password)
    owns_http = http is None
    client = httpx.AsyncClient(timeout=30.0) if owns_http else http
    try:
        # SnapshotStore needs a file (no `:memory:`), so it lives in a temp dir that is removed on exit.
        with tempfile.TemporaryDirectory(prefix="akd-portfolio-") as tmp:
            store = SnapshotStore(Path(tmp) / "snapshots.db")
            try:
                daemon = AkdDaemon(account=_ACCOUNT_KEY, store=store, http=client, creds=creds)
                try:
                    parsed = await daemon.login()
                except (httpx.HTTPError, OSError) as exc:
                    raise PortfolioError("akd_unreachable", f"could not reach AKD: {type(exc).__name__}") from None
                if not parsed["valid"]:
                    raise PortfolioError("login_rejected", f"AKD rejected the login: {parsed.get('raw') or 'no reason given'}")
                try:
                    poll = await daemon.poll_once()
                except (httpx.HTTPError, OSError) as exc:
                    raise PortfolioError("akd_unreachable", f"could not reach AKD: {type(exc).__name__}") from None
                return {
                    "account": user_id,
                    "fetched_at": datetime.now(UTC).isoformat(),
                    "holdings": reads.holdings(store, _ACCOUNT_KEY),
                    "cash": reads.cash(store, _ACCOUNT_KEY),
                    "orders": reads.orders(store, _ACCOUNT_KEY),
                    "poll": poll,
                    "last_error": daemon.status()["last_error"],
                }
            finally:
                store.close()
    finally:
        if owns_http:
            await client.aclose()


def accounts_from_env(env: Mapping[str, str]) -> list[tuple[str, str]]:
    """The accounts configured in `env`, in order, as `(user_id, password)`.

    Account 1 is `AKD_USER_ID` / `AKD_PASSWORD` (or the `_1` spelling, not both); further accounts are
    `AKD_USER_ID_2` / `AKD_PASSWORD_2`, `_3`, ... Slots are sorted numerically. A slot with a blank
    user id is skipped; a user id without a password is kept with a blank password so the caller
    can report it for that account instead of silently dropping it."""
    slots: dict[int, tuple[str, str]] = {}
    for key, value in env.items():
        match = _USER_VAR.match(key)
        if not match or not (value or "").strip():
            continue
        suffix = match.group(1)
        slot = int(suffix) if suffix else 1
        if slot in slots:
            raise PortfolioError(
                "invalid_config", "set either AKD_USER_ID or AKD_USER_ID_1 for the first account, not both"
            )
        password_keys = ["AKD_PASSWORD", "AKD_PASSWORD_1"] if slot == 1 else [f"AKD_PASSWORD_{slot}"]
        password = next((env[k] for k in password_keys if env.get(k)), "")
        slots[slot] = (value.strip(), password)
    return [slots[n] for n in sorted(slots)]


def run_portfolio(env: Mapping[str, str], out, err, *, http=None, http_for: Callable[[str], object] | None = None) -> int:
    """The `portfolio` command: accounts from `env`, exactly one JSON document to `out`, exit code
    returned. `err` is for diagnostics only. Output is `{"accounts": [...]}`, one entry per account in
    order: the portfolio, or `{"account", "error", "message"}` for one that failed. Each account gets
    ONE login attempt and a failure never stops the next account. Exit code: 0 if every account
    worked, else the first failure's: 2 missing_credentials/invalid_config/duplicate_account,
    3 login_rejected, 4 akd_unreachable. `http` / `http_for(user_id)` inject fakes for tests."""
    # The daemon's INFO login diagnostic carries the account id and password length: keep it off.
    akd_logger = logging.getLogger("akd")
    previous = akd_logger.level
    akd_logger.setLevel(logging.WARNING)
    code = 0
    try:
        try:
            accounts = accounts_from_env(env)
        except PortfolioError as exc:
            result: dict = {"error": exc.code, "message": exc.message}
            code = EXIT_CODES[exc.code]
        else:
            if not accounts:
                result = {
                    "error": "missing_credentials",
                    "message": "set AKD_USER_ID and AKD_PASSWORD (more accounts: AKD_USER_ID_2 and AKD_PASSWORD_2, ...)",
                }
                code = EXIT_CODES["missing_credentials"]
            else:
                entries, seen = [], set()
                for user_id, password in accounts:
                    try:
                        if user_id in seen:
                            raise PortfolioError("duplicate_account", "listed more than once; logged in only once")
                        seen.add(user_id)
                        client = http_for(user_id) if http_for else http
                        entries.append(asyncio.run(fetch_portfolio(user_id, password, http=client)))
                    except PortfolioError as exc:
                        entries.append({"account": user_id, "error": exc.code, "message": exc.message})
                        code = code or EXIT_CODES[exc.code]
                result = {"accounts": entries}
    finally:
        akd_logger.setLevel(previous)
    if code:
        print(f"akd portfolio: exit {code}", file=err)
    json.dump(result, out, indent=2, default=str)
    out.write("\n")
    return code
