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

from akd import daemon as service
from akd.daemon import AkdDaemon
from akd.protocol.codec import encode_message
from akd.protocol.session import build_heartbeat
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


def test_reconnect_dispatches_each_message_exactly_once(tmp_path):
    d = AkdDaemon(account="ACCT-TEST", store=AkdStore(tmp_path / "b.db"))
    seen = []
    d.on_message = seen.append  # test hook
    wire = build_heartbeat("ACCT-TEST") + build_heartbeat("ACCT-TEST")
    # simulate a drop mid-second-frame: deliver in two chunks across a
    # "reconnect", carrying the session buffer - no replay, no loss.
    cut = len(wire) - 4
    d.dispatch(d._session.feed(wire[:cut]))
    d.dispatch(d._session.feed(wire[cut:]))
    assert seen.count(["MSG-CLIENT", "HI_FROM_CLIENT", "ACCT-TEST"]) == 2


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


def test_delimiter_in_session_token_does_not_crash_the_handshake(tmp_path):
    d = AkdDaemon(account=ACCOUNT, store=AkdStore(tmp_path / "b.db"))
    d._session.token = "bad|token"

    frame = d._safe_handshake_step([])  # connecting -> authenticating: sends NEW_CLIENT
    assert frame is not None
    assert d._session.state == "authenticating"

    ack = encode_message(["SERVER_ACK", "1"])
    from akd.protocol.codec import iter_messages

    messages, _ = iter_messages(ack)
    frame = d._safe_handshake_step(messages)  # would build AUTHENTICATE_PARTIAL with the bad token

    assert frame is None  # caught, not raised
    assert d._last_error is not None
    assert "cannot frame" in d._last_error


# --- socket loop: reconnect with backoff, then a prompt, clean stop() ------


class FakeStream:
    """A minimal async reader/writer double. Each entry in `reads` is either
    bytes to hand back from the next `read()` or an Exception to raise -
    standing in for a real `asyncio.StreamReader`/`StreamWriter` pair with
    NO socket anywhere."""

    def __init__(self, reads=()):
        self._reads = list(reads)
        self.written: list[bytes] = []
        self.closed = False

    async def read(self, n=4096):
        if self._reads:
            item = self._reads.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        await asyncio.sleep(3600)  # idle: nothing more scripted, blocks until cancelled
        return b""

    def write(self, data):
        self.written.append(data)

    async def drain(self):
        return None

    def close(self):
        self.closed = True

    async def wait_closed(self):
        return None


def _make_connector(streams):
    it = iter(streams)

    async def connector():
        s = next(it)
        return s, s

    return connector


async def test_socket_loop_reconnects_with_backoff_and_stop_closes_the_socket_promptly(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AKD_PASSWORD", "x")
    monkeypatch.setattr(service, "BACKOFF_BASE_SECONDS", 0.01)
    monkeypatch.setattr(service, "MAX_BACKOFF_SECONDS", 0.02)
    monkeypatch.setattr(service, "READ_TIMEOUT_SECONDS", 0.02)

    ack1 = encode_message(["SERVER_ACK", "1"])
    ack2 = encode_message(["SERVER_ACK", "2"])
    stream1 = FakeStream(reads=[ConnectionResetError("dropped mid-handshake")])
    stream2 = FakeStream(reads=[ack1, ack2])

    http = RoutingFakeHTTP(auth_body="<return>true|TOK-SESSION|Trader|</return>")
    d = AkdDaemon(
        account=ACCOUNT,
        store=AkdStore(tmp_path / "b.db"),
        http=http,
        transport=_make_connector([stream1, stream2]),
    )
    seen = []
    d.on_message = seen.append

    task = asyncio.create_task(d.run())
    # Give the reconnect (drop -> backoff -> second connection -> full
    # handshake -> a couple of heartbeat cycles) time to play out.
    await asyncio.sleep(0.3)

    await asyncio.wait_for(d.stop(), timeout=1)
    await asyncio.wait_for(task, timeout=1)  # stop() must let run() finish promptly

    assert stream1.closed is True  # the dropped connection's socket was closed, not leaked
    assert stream2.closed is True  # and so was the live one, on stop()
    assert seen.count(["MSG-CLIENT", "SERVER_ACK", "1"]) == 1  # exactly once, no replay
    assert seen.count(["MSG-CLIENT", "SERVER_ACK", "2"]) == 1
    assert d._session.state == "live"  # the second connection actually completed the handshake


# --- status(): honest about what is and isn't confirmed --------------------


def test_status_authenticated_reflects_only_the_soap_login_not_socket_framing(tmp_path):
    d = AkdDaemon(account=ACCOUNT, store=AkdStore(tmp_path / "b.db"))
    status = d.status()
    assert status["authenticated"] is False
    assert status["socket_ack_confirmed"] is False  # never claimed true - no captured ack exists


# --- fix round 1, Important: a rejected login must stop run(), not proceed -


class RejectingHTTP:
    """Always answers login with a rejection; counts every POST so a test
    can assert nothing beyond login was ever sent."""

    def __init__(self):
        self.calls = 0

    async def post(self, url, content=None, headers=None):
        self.calls += 1

        class R:
            text = "<return>false|bad credentials</return>"

        return R()


def _poisoned_transport():
    """A `transport` factory that fails the test loudly if the trade socket
    is ever opened - used to prove `run()` never reaches `_run_socket` after
    a rejected login."""

    async def connector():
        raise AssertionError(
            "the trade socket must never be opened after a rejected login"
        )

    return connector


async def test_run_stops_without_starting_loops_when_login_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("AKD_PASSWORD", "x")
    http = RejectingHTTP()
    d = AkdDaemon(
        account=ACCOUNT,
        store=AkdStore(tmp_path / "b.db"),
        http=http,
        transport=_poisoned_transport(),
    )

    # A rejected login must make run() return promptly on its own - if it
    # instead started the poll/socket loops, this would hang (poll_once
    # would eventually 500 against RejectingHTTP's login-shaped body, and
    # the poisoned transport would raise from inside _run_socket's own
    # try/except, which swallows it and reconnects forever) until the
    # timeout below fires and fails the test.
    await asyncio.wait_for(d.run(), timeout=1)

    assert http.calls == 1  # login only - no GetCollateral/GetOrderHistory POSTs ever sent
    status = d.status()
    assert status["authenticated"] is False
    assert status["last_error"] is not None
    assert "reject" in status["last_error"].lower()


async def test_run_propagates_a_transient_login_failure_instead_of_treating_it_as_a_rejection(
    tmp_path, monkeypatch
):
    """A login that never produces a valid/invalid verdict at all (the SOAP
    call itself blew up - no network, a timeout) is NOT a rejection: it must
    surface as a real exception out of run(), distinct from the clean,
    silent stop a rejected login produces above."""

    class BrokenHTTP:
        async def post(self, url, content=None, headers=None):
            raise ConnectionError("could not reach the SOAP endpoint")

    monkeypatch.setenv("AKD_PASSWORD", "x")
    d = AkdDaemon(
        account=ACCOUNT,
        store=AkdStore(tmp_path / "b.db"),
        http=BrokenHTTP(),
        transport=_poisoned_transport(),
    )

    with pytest.raises(ConnectionError):
        await asyncio.wait_for(d.run(), timeout=1)

    assert d.status()["authenticated"] is False


# --- fix round 1, Minor: backoff resets on a genuinely-live connection -----


async def test_backoff_resets_the_instant_a_connection_actually_goes_live(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "BACKOFF_BASE_SECONDS", 1.0)
    d = AkdDaemon(account=ACCOUNT, store=AkdStore(tmp_path / "b.db"))
    d._backoff = 16.0  # simulate a maxed-out backoff carried over from earlier failed attempts

    def stop_once_authenticated(message):
        if message[-1] == "2":  # the second ack - the one that flips state to "live"
            d._stop_requested.set()

    d.on_message = stop_once_authenticated

    ack1 = encode_message(["SERVER_ACK", "1"])
    ack2 = encode_message(["SERVER_ACK", "2"])
    stream = FakeStream(reads=[ack1, ack2])

    await d._drive_connection(stream, stream)

    assert d._session.state == "live"
    assert d._backoff == service.BACKOFF_BASE_SECONDS  # reset on success, not left maxed out


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
