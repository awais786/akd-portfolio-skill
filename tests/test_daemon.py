"""Task 12: the direct-connect daemon (login -> poll reads -> store).

No real socket, no real credentials, no real account values anywhere in
this suite (`AKD_PASSWORD` is set to a synthetic placeholder via
`monkeypatch.setenv`, never read from a real environment). `http` and
`transport` are always the injected fakes below - `asyncio_mode = "auto"`
(pyproject.toml) runs the `async def` tests directly; the two given tests
use `asyncio.run(...)` explicitly instead, matching the brief verbatim.

The one exception is `test_live_login_round_trip`, marked `@pytest.mark.live`
and skipped unless a real operator supplies real credentials by hand - see
its own docstring.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from akd.daemon import AkdDaemon
from akd.store import SnapshotStore as AkdStore

ACCOUNT = "ACCT-TEST"


class FakeHTTP:
    """Returns a canned GetCollateral <return> body for any POST."""

    def __init__(self, body):
        self._body = body

    async def post(self, url, content=None, headers=None):
        class R:
            text = self._body

        return R()


def test_poll_once_decodes_and_stores_collateral(tmp_path, monkeypatch):
    monkeypatch.setenv("AKD_PASSWORD", "x")
    body = (
        "<return>Symbol;Quantity;TotalQty;Avg. BUY RATE|"
        "OGDC;100;100;85.5|</return>"
    )
    d = AkdDaemon(
        account="ACCT-TEST",
        store=AkdStore(tmp_path / "b.db"),
        http=FakeHTTP(body),
    )
    results = asyncio.run(d.poll_once())
    assert results["GetCollateral"] is True
    snap = AkdStore(tmp_path / "b.db").latest("ACCT-TEST", "GetCollateral")
    assert snap["payload"]["positions"][0]["symbol"] == "OGDC"


# --- login: env-only password, real token as user_id, honest auth gate ----


class RoutingFakeHTTP:
    """Routes on the operation name embedded in the request envelope, so a
    single fake can answer login and both read operations distinctly - and
    records every posted body for assertion."""

    def __init__(self, *, auth_body="<return></return>", collateral_body="<return></return>",
                 order_history_body="<return>No Record</return>"):
        self.auth_body = auth_body
        self.collateral_body = collateral_body
        self.order_history_body = order_history_body
        self.requests: list[str] = []

    async def post(self, url, content=None, headers=None):
        self.requests.append(content)
        if "SecurecustomAuthentication" in content:
            text = self.auth_body
        elif "GetCollateral" in content:
            text = self.collateral_body
        elif "GetOrderHistory" in content:
            text = self.order_history_body
        else:  # pragma: no cover - defensive, no such op is exercised here
            text = "<return></return>"

        class R:
            pass

        r = R()
        r.text = text
        return r


def test_login_reads_password_from_env_and_never_logs_it(tmp_path, monkeypatch, caplog):
    secret = "sekret-do-not-log-me"
    monkeypatch.setenv("AKD_PASSWORD", secret)
    http = RoutingFakeHTTP(auth_body="<return>true|TOK-SESSION|Trader|</return>")
    d = AkdDaemon(account=ACCOUNT, store=AkdStore(tmp_path / "b.db"), http=http)

    with caplog.at_level("DEBUG"):
        result = asyncio.run(d.login())

    assert result["valid"] is True
    assert secret not in http.requests[0]  # RSA-encrypted on the wire, never plaintext
    assert secret not in caplog.text


def test_login_valid_sets_user_id_to_real_token_not_a_placeholder(tmp_path, monkeypatch):
    monkeypatch.setenv("AKD_PASSWORD", "x")
    http = RoutingFakeHTTP(auth_body="<return>true|TOK-SESSION|Trader|</return>")
    d = AkdDaemon(account=ACCOUNT, store=AkdStore(tmp_path / "b.db"), http=http)

    asyncio.run(d.login())
    assert d._user_id == "TOK-SESSION"

    # Reads use the ACCOUNT as UserID, not the login token: the live server
    # rejects the token here with "Invalid Account" (the token "13DO"-style
    # value is a trader/session code, confirmed against a captured
    # GetExposureDynamic whose UserID is the account). The token is still kept
    # on the session for the socket layer (asserted above).
    asyncio.run(d.poll_once())
    assert f"<UserID>{ACCOUNT}</UserID>" in http.requests[-2]  # the GetCollateral request


def test_login_invalid_does_not_authenticate_and_keeps_account_as_user_id(tmp_path, monkeypatch):
    monkeypatch.setenv("AKD_PASSWORD", "x")
    http = RoutingFakeHTTP(auth_body="<return>false|badlogin</return>")
    d = AkdDaemon(account=ACCOUNT, store=AkdStore(tmp_path / "b.db"), http=http)

    result = asyncio.run(d.login())
    assert result["valid"] is False
    assert d.status()["authenticated"] is False
    assert d._user_id == ACCOUNT  # never overwritten with an invalid/placeholder token


# --- poll_once: absence stays absence --------------------------------------


def test_poll_once_does_not_store_a_decode_failure_as_an_empty_book(tmp_path):
    body = "<return>Symbol;Quantity;TotalQty;Avg. BUY RATE|BADROW|</return>"
    store = AkdStore(tmp_path / "b.db")
    d = AkdDaemon(account=ACCOUNT, store=store, http=FakeHTTP(body))

    results = asyncio.run(d.poll_once())
    assert results["GetCollateral"] is False
    assert store.latest(ACCOUNT, "GetCollateral") is None  # absence, never a fabricated []


def test_poll_once_stores_confirmed_empty_book_when_collateral_body_is_blank(tmp_path):
    http = RoutingFakeHTTP(collateral_body="<return></return>")
    store = AkdStore(tmp_path / "b.db")
    d = AkdDaemon(account=ACCOUNT, store=store, http=http)

    results = asyncio.run(d.poll_once())
    assert results["GetCollateral"] is True
    snap = store.latest(ACCOUNT, "GetCollateral")
    assert snap["payload"]["positions"] == []  # a genuinely confirmed empty book


def test_poll_once_stores_confirmed_empty_history_on_no_record_sentinel(tmp_path):
    http = RoutingFakeHTTP(order_history_body="<return>No Record</return>")
    store = AkdStore(tmp_path / "b.db")
    d = AkdDaemon(account=ACCOUNT, store=store, http=http)

    results = asyncio.run(d.poll_once())
    assert results["GetOrderHistory"] is True
    snap = store.latest(ACCOUNT, "GetOrderHistory")
    assert snap["payload"]["orders"] == []


def test_poll_once_does_not_store_order_history_decode_failure(tmp_path):
    http = RoutingFakeHTTP(order_history_body="<return>garbled;not;a;fill</return>")
    store = AkdStore(tmp_path / "b.db")
    d = AkdDaemon(account=ACCOUNT, store=store, http=http)

    results = asyncio.run(d.poll_once())
    assert results["GetOrderHistory"] is False
    assert store.latest(ACCOUNT, "GetOrderHistory") is None


# --- carry: a delimiter in a server-supplied token must not crash the loop -


# --- socket loop: reconnect with backoff, then a prompt, clean stop() ------


def _make_connector(streams):
    it = iter(streams)

    async def connector():
        s = next(it)
        return s, s

    return connector


# --- status(): honest about what is and isn't confirmed --------------------


# --- fix round 1, Important: a rejected login must stop run(), not proceed -


def _poisoned_transport():
    """A `transport` factory that fails the test loudly if the trade socket
    is ever opened - used to prove `run()` never reaches `_run_socket` after
    a rejected login."""

    async def connector():
        raise AssertionError(
            "the trade socket must never be opened after a rejected login"
        )

    return connector


# --- fix round 1, Minor: backoff resets on a genuinely-live connection -----


# --- manual-only: a real login round trip, never run in CI -----------------


@pytest.mark.live
@pytest.mark.skipif(
    not os.environ.get("AKD_PASSWORD") or not os.environ.get("AKD_ACCOUNT"),
    reason=(
        "manual-only: requires a real AKD_PASSWORD/AKD_ACCOUNT and "
        "network access to online.akdtrade.biz - never run in CI"
    ),
)
def test_live_login_round_trip(tmp_path):
    """Manual verification only. Confirms the real SecurecustomAuthentication
    round trip against AKD's live SOAP endpoint. Run explicitly with:

        AKD_PASSWORD=... AKD_ACCOUNT=... \\
            uv run pytest -m live tests/test_daemon.py::test_live_login_round_trip
    """
    import httpx

    d = AkdDaemon(
        account=os.environ["AKD_ACCOUNT"],
        store=AkdStore(tmp_path / "live.db"),
        http=httpx.AsyncClient(),
    )
    result = asyncio.run(d.login())
    assert result["valid"] is True


def test_status_reports_only_login_state_and_last_error(tmp_path):
    d = AkdDaemon(account=ACCOUNT, store=AkdStore(tmp_path / "b.db"))
    assert d.status() == {
        "account": ACCOUNT,
        "authenticated": False,
        "last_error": None,
        "capabilities": ["reads"],
    }
