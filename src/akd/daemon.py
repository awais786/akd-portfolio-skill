"""The long-lived AKD connection process (protocol map §1-5). SOAP login +
read polling + socket session/heartbeat, writing the same store the read
tools read.

Reads `PSX_AKD_PASSWORD` from the environment, never as a parameter, never
logged (`PSX_AKD_PIN` is declared in the manifest but this READS daemon
never places orders, so it is not used here). All network is injectable
(`http`, `transport`) so the whole daemon is testable with NO live broker
connection - see `brokers/akd/tests/test_daemon.py`.

Two carries from earlier tasks this module has to live with honestly:

1. The captures never show a confirmed server->client ack for the trade
   socket's `AUTHENTICATE_PARTIAL` handshake (Task 10's `session.py`
   docstring). `SessionState` therefore validates FRAMING only, not real ack
   content, and this daemon does not report the socket as "authenticated"
   from that framing. The SOAP login's `parse_auth_response(...)["valid"]`
   is the one authoritative "logged in" signal (`self._authenticated`);
   `status()` reports the socket's handshake state separately and marks its
   ack as unconfirmed, so a caller can never mistake framing success for a
   real login.
2. A server-supplied value (the post-login session token, used as both the
   SOAP `user_id` and the socket session's `token`) could contain '|' or
   '*' - the codec's own framing delimiters - which `encode_message` (Task
   8) refuses to encode, raising `ValueError`. Every place this daemon asks
   the session/codec layer to build a socket frame from such a value goes
   through `_safe_handshake_step`/`_safe_send`, which catch that and record
   it as `_last_error` instead of letting it crash the read loop.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime
from pathlib import Path

from . import credentials as _rt
from .config import SERVER_CODE, SOAP_URL, SOCKET_HOST, SOCKET_PORT
from .protocol import soap
from .protocol.crypto import load_public_key
from .protocol.decode import (
    decode_collateral,
    decode_exposure,
    decode_order_history,
    maybe_decompress,
)
from .protocol.session import SessionState, build_heartbeat
from .store import SnapshotStore

__all__ = ["AkdDaemon"]

log = logging.getLogger("akd.daemon")

# Trade socket host/port from the protocol map (§1, args.txt's tradeCastIP /
# tradeCastServerPort#8092) - env-overridable the same way SOAP_URL is, so a
# test or a future sandbox target never has to edit this file.

POLL_SECONDS = 5
BACKOFF_BASE_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 30.0
READ_TIMEOUT_SECONDS = 30.0  # bounds each socket read so stop() is noticed promptly

_HEADERS = {"Content-Type": "text/xml"}

# The TradeCast SOAP service (JAX-WS) needs exactly what the Java client sends,
# or it NullPointerExceptions before it ever looks at the credentials:
#   - a service-level HTTP Basic credential baked into the client
#     (base64 "myservice:12345678"), the same for every user;
#   - a per-operation SOAPAction that routes the request to its handler.
# Both were recovered from the client's captured request headers.
_SERVICE_AUTH = "Basic bXlzZXJ2aWNlOjEyMzQ1Njc4"


def _soap_headers(operation: str) -> dict[str, str]:
    return {
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": f'"http://login/LoginServer/{operation}Request"',
        "Authorization": _SERVICE_AUTH,
    }
_KEY = Path(__file__).resolve().parent / "public.key"

class AkdDaemon:
    """Owns one AKD account's SOAP login, read polling, and trade-socket
    session for as long as `run()` is awaited.

    `http` (an `httpx.AsyncClient`-shaped object: `async def post(url, *,
    content, headers)`) and `transport` (an async zero-arg callable
    returning an `(reader, writer)` pair, each shaped like
    `asyncio.StreamReader`/`StreamWriter`: `async read(n)`, `write(data)`,
    optionally `async drain()`, `close()`, `async wait_closed()`) are the two
    injectable seams. Neither is opened by default until `run()`'s socket
    loop actually needs one - `login()`/`poll_once()` only ever touch
    `http`, so a test can exercise them with no `transport` at all (see the
    given test suite).
    """

    def __init__(
        self,
        *,
        account: str,
        store: SnapshotStore,
        http=None,
        transport=None,
    ) -> None:
        self.account = account
        self._store = store
        self._http = http
        self._transport = transport
        self._session = SessionState(account=account)
        self._public_key = load_public_key(_KEY)
        self._authenticated = False
        self._auth_result: dict | None = None
        # Default to `account` (matching every soap.py builder test's own
        # convention of user_id == account) until a real login hands us a
        # token - never a made-up placeholder once we have the real one.
        self._user_id = account
        self.on_message = None  # optional test/inspection hook, called once per dispatched message
        self._last_error: str | None = None
        self._stop_requested = asyncio.Event()
        self._current_writer = None
        # Trade-socket reconnect backoff, in seconds. Lives on the instance
        # (not a local in `_run_socket`) so `_drive_connection` can reset it
        # the instant a connection genuinely reaches `live` - a connection
        # that stayed up for minutes before dropping must reconnect
        # promptly, not inherit whatever backoff an earlier failed attempt
        # had climbed to.
        self._backoff = BACKOFF_BASE_SECONDS

    # -- SOAP: login + reads -------------------------------------------------

    async def login(self) -> dict:
        """POST `SecurecustomAuthentication` and record the result.

        `PSX_AKD_PASSWORD` is read here, from the environment, every call -
        never accepted as a parameter, never included in any log line. The
        parsed `valid` flag (never socket framing) is what sets
        `self._authenticated`; see the module docstring's carry (1).
        """
        # Credentials come from the web UI (runtime holder), falling
        # back to PSX_AKD_PASSWORD for headless/CI. Nothing here is logged.
        password = _rt.password()
        if not password:
            # No credentials yet: report a clean rejection so the supervisor
            # stops the daemon (status carries this) instead of crashing on a
            # KeyError. Logging in from the terminal sets them and restarts.
            parsed = {"valid": False, "token": None,
                      "raw": "no AKD credentials — log in from the web UI"}
            self._auth_result = parsed
            self._authenticated = False
            return parsed
        login_user = _rt.login_user_id(self.account)
        # Positional login (recovered from LoginForm2, confirmed against a
        # captured request): send the chars at 4 random distinct positions and
        # those positions, never the full password. See soap.partial_password_fields.
        positions = soap.choose_login_positions(len(password))
        partial, position_field = soap.partial_password_fields(password, positions)
        body = soap.build_secure_auth(
            login_user, partial, position_field, self._public_key, server_code=SERVER_CODE
        )
        resp = await self._http.post(SOAP_URL, content=body, headers=_soap_headers("SecurecustomAuthentication"))
        parsed = soap.parse_auth_response(resp.text)
        # Full request/response diagnostics. NOTHING secret is logged: the four
        # challenge characters are NOT recorded (only how many, and which
        # positions); the response is the server's own text. This is what makes
        # a failure legible instead of a guess.
        status = getattr(resp, "status_code", None)
        parsed["http_status"] = status
        parsed["response_body"] = resp.text
        diag = {
            "url": SOAP_URL, "user": login_user, "server_code": SERVER_CODE,
            "password_len": len(password), "positions": positions,
            "request_bytes": len(body), "http_status": status,
            "response_len": len(resp.text), "response_body": resp.text[:800],
            "parsed_valid": parsed["valid"], "parsed_raw": parsed.get("raw"),
        }
        log.info("AKD login diagnostic: %s", diag)
        self._auth_result = parsed
        self._authenticated = parsed["valid"]
        if parsed["valid"] and parsed.get("token"):
            # The real post-auth session id, never a placeholder - used as
            # `user_id` on every subsequent read request and as the trade
            # socket's own session token.
            self._user_id = parsed["token"]
        return parsed

    def _soap_account(self) -> str:
        """The AKD account for SOAP calls (terminal-set), distinct from
        `self.account` which keys the local snapshot store."""
        return _rt.login_user_id(self.account)

    async def poll_once(self) -> dict[str, bool]:
        """One pass over every read operation this plugin decodes.

        Absence is never emptiness: a body that fails to decode (non-blank
        input yielding zero rows) is recorded as a failed poll, not stored
        as a confirmed-empty book (mirrors
        `apps/ingest/src/ingest/akd_worker.py`'s guard).
        """
        now = datetime.now(UTC)
        return {
            "GetCollateral": await self._poll_collateral(now),
            "GetOrderHistory": await self._poll_order_history(now),
            "GetExposureDynamic": await self._poll_exposure(now),
        }

    async def _poll_collateral(self, now: datetime) -> bool:
        resp = await self._http.post(
            SOAP_URL,
            content=soap.build_getcollateral(self._soap_account(), self._soap_account()),
            headers=_soap_headers("GetCollateral"),
        )
        body = soap.extract_return(resp.text)
        if body is None:
            self._last_error = "GetCollateral: no <return> element in response"
            log.warning(self._last_error)
            return False

        positions = decode_collateral(body, as_of=now)
        if not positions and body.strip():
            # A non-blank body decoding to zero positions is a decode
            # failure (garbled response, expired session, unrecognised
            # layout) - NOT TradeCast confirming an empty book. Storing it
            # would be exactly the "you own nothing" lie this whole path
            # exists to prevent, through the dedupe door instead of the
            # collateral parser's.
            self._last_error = (
                "GetCollateral: non-blank body decoded to 0 positions - not stored"
            )
            log.warning(self._last_error)
            return False

        payload = {
            "positions": [
                {"symbol": p.symbol, "quantity": p.quantity, "avg_cost": p.avg_cost}
                for p in positions
            ]
        }
        return self._store.put(self.account, "GetCollateral", now, payload)

    async def _poll_exposure(self, now: datetime) -> bool:
        """Poll GetExposureDynamic -> cash / exposure / FREE CASH (buying power)."""
        try:
            resp = await self._http.post(
                SOAP_URL,
                content=soap.build_getexposuredynamic(self._soap_account()),
                headers=_soap_headers("GetExposureDynamic"),
            )
            body = soap.extract_return(resp.text)
            money = decode_exposure(body) if body else None
            if not money:
                return False
            return self._store.put(self.account, "GetExposureDynamic", now, money)
        except Exception as exc:  # noqa: BLE001 - never let a read failure crash the loop
            self._last_error = f"GetExposureDynamic: {exc}"
            return False

    async def _poll_order_history(self, now: datetime) -> bool:
        resp = await self._http.post(
            SOAP_URL,
            content=soap.build_getorderhistory(self._soap_account(), self._soap_account()),
            headers=_soap_headers("GetOrderHistory"),
        )
        body = soap.extract_return(resp.text)
        if body is None:
            self._last_error = "GetOrderHistory: no <return> element in response"
            log.warning(self._last_error)
            return False

        # The "No Record" sentinel must be checked on the DECODED text - it
        # may ship base64+gzip, and maybe_decompress is idempotent on
        # already-plaintext input, so decoding it here and letting
        # decode_order_history decode again internally is always safe
        # (mirrors apps/ingest/src/ingest/akd_worker.py).
        decoded_body = maybe_decompress(body)
        if "No Record" in decoded_body:
            # A confirmed, legitimately empty history - not a decode
            # failure, so this one genuinely stores an empty list.
            return self._store.put(self.account, "GetOrderHistory", now, {"orders": []})

        orders = decode_order_history(body, as_of=now)
        if not orders and decoded_body.strip():
            self._last_error = (
                "GetOrderHistory: non-blank body decoded to 0 fills - not stored"
            )
            log.warning(self._last_error)
            return False

        payload = {
            "orders": [
                {
                    "order_id": o.order_id,
                    "symbol": o.symbol,
                    "side": o.side,
                    "quantity": o.quantity,
                    "price": o.price,
                    "status": o.status,
                    "placed_at": o.placed_at.isoformat(),
                }
                for o in orders
            ]
        }
        return self._store.put(self.account, "GetOrderHistory", now, payload)

    # -- trade socket ----------------------------------------------------

    def dispatch(self, messages: list[list[str]]) -> None:
        """Route each already-decoded inbound socket message to `on_message`
        exactly once. Pure routing - reassembly/no-loss/no-duplication is
        `SessionState.feed`'s job (Task 10), not this method's."""
        for message in messages:
            if self.on_message is not None:
                self.on_message(message)

    def _safe_handshake_step(self, messages: list[list[str]]) -> bytes | None:
        """`SessionState.handshake_step`, guarded against a server-supplied
        value (e.g. our own post-login token, echoed into
        `AUTHENTICATE_PARTIAL`) containing a framing delimiter the codec
        cannot encode (module docstring carry (2))."""
        try:
            return self._session.handshake_step(messages)
        except ValueError as exc:
            self._last_error = f"cannot frame socket handshake message: {exc}"
            log.error(self._last_error)
            return None

    async def _safe_send(self, writer, builder, *args) -> bool:
        """Build a socket frame with `builder(*args)` and write it, catching
        the same framing-delimiter failure as `_safe_handshake_step` instead
        of letting it crash the daemon loop."""
        try:
            frame = builder(*args)
        except ValueError as exc:
            self._last_error = f"cannot frame socket message: {exc}"
            log.error(self._last_error)
            return False
        writer.write(frame)
        drain = getattr(writer, "drain", None)
        if drain is not None:
            await drain()
        return True

    async def _connect(self):
        """Open the trade socket. Uses the injected `transport` factory when
        given (every test does this - no live connection ever runs in the
        suite); otherwise opens a real TCP connection."""
        if self._transport is not None:
            return await self._transport()
        return await asyncio.open_connection(SOCKET_HOST, SOCKET_PORT)

    async def _read_with_timeout(self, reader) -> bytes | None:
        """Bounds every read so the loop wakes up often enough to notice
        `stop()` even with no traffic - returns `None` on a timeout (not an
        error), distinct from `b""` (peer closed)."""
        try:
            return await asyncio.wait_for(reader.read(4096), timeout=READ_TIMEOUT_SECONDS)
        except TimeoutError:
            return None

    async def _close_writer(self, writer) -> None:
        if writer is None:
            return
        try:
            writer.close()
            wait_closed = getattr(writer, "wait_closed", None)
            if wait_closed is not None:
                await wait_closed()
        except Exception as exc:  # noqa: BLE001 - closing must never raise into a caller
            log.warning("error closing trade socket: %s", exc)

    async def _drive_connection(self, reader, writer) -> None:
        """One connection's lifetime: drive the handshake to `live`, then
        heartbeat/read until the socket drops or `stop()` is called."""
        frame = self._safe_handshake_step([])
        if frame:
            writer.write(frame)

        while self._session.state != "live" and not self._stop_requested.is_set():
            chunk = await self._read_with_timeout(reader)
            if chunk is None:
                continue  # read timed out - nothing arrived, recheck stop flag
            if chunk == b"":
                raise ConnectionError("trade socket closed by peer during handshake")
            messages = self._session.feed(chunk)
            self.dispatch(messages)
            frame = self._safe_handshake_step(messages)
            if frame:
                writer.write(frame)

        if self._session.state == "live":
            # Reached `live` for real (not just "the loop above exited
            # because stop was requested mid-handshake") - reset the
            # reconnect backoff here, at the moment success actually
            # happened, rather than after `_drive_connection` returns:
            # a connection that goes live and later drops raises out of the
            # heartbeat loop below, which would otherwise skip a
            # post-return reset entirely and reconnect at whatever backoff
            # an earlier failed attempt had climbed to.
            self._backoff = BACKOFF_BASE_SECONDS

        while not self._stop_requested.is_set():
            await self._safe_send(writer, build_heartbeat, self.account)
            chunk = await self._read_with_timeout(reader)
            if chunk is None:
                continue
            if chunk == b"":
                raise ConnectionError("trade socket closed by peer")
            messages = self._session.feed(chunk)
            self.dispatch(messages)

    async def _run_socket(self) -> None:
        """Own the trade-socket connection for the daemon's lifetime:
        connect, drive one connection's handshake/heartbeat loop, and on any
        drop reconnect with capped exponential backoff (never a tight
        reconnect loop). `SessionState.reset()` on every reconnect discards
        the dead connection's buffered remainder - a half-frame from a
        socket that will never send its other half must not be spliced onto
        bytes from the new connection (Task 10's own `reset()` contract);
        within one connection, `SessionState.feed`'s remainder buffer is
        what gives exactly-once, no-loss reassembly. The backoff itself
        (`self._backoff`) is reset inside `_drive_connection` the instant a
        connection actually reaches `live`, not here - see that method."""
        self._backoff = BACKOFF_BASE_SECONDS
        while not self._stop_requested.is_set():
            writer = None
            try:
                reader, writer = await self._connect()
                self._current_writer = writer
                self._session.reset()
                await self._drive_connection(reader, writer)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - one dropped socket must not kill the daemon
                self._last_error = f"trade socket error: {exc}"
                log.warning(self._last_error)
            finally:
                await self._close_writer(writer)
                self._current_writer = None

            if self._stop_requested.is_set():
                break
            await asyncio.sleep(min(self._backoff, MAX_BACKOFF_SECONDS))
            self._backoff = min(self._backoff * 2, MAX_BACKOFF_SECONDS)

    async def _poll_loop(self) -> None:
        while not self._stop_requested.is_set():
            try:
                await self.poll_once()
            except Exception as exc:  # noqa: BLE001 - one failed poll must not kill the daemon
                self._last_error = f"poll failed: {exc}"
                log.warning(self._last_error)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop_requested.wait(), timeout=POLL_SECONDS)

    # -- lifecycle ---------------------------------------------------------

    def status(self) -> dict:
        """Report what is and isn't confirmed. `authenticated` reflects only
        the SOAP login's `valid` flag; the trade socket's own handshake
        state is reported separately, and its ack is explicitly flagged as
        unconfirmed - see the module docstring's carry (1)."""
        return {
            "account": self.account,
            "authenticated": self._authenticated,
            "socket_state": self._session.state,
            "socket_ack_confirmed": False,  # no captured server->client ack exists (Task 10)
            "last_error": self._last_error,
            "capabilities": ["reads"],
        }

    async def run(self) -> None:
        """Supervised coroutine: login, then (only on a successful login)
        poll reads and hold the trade socket concurrently until cancelled or
        `stop()`'d. Responds to `CancelledError` by closing the socket
        rather than swallowing the cancellation - a supervisor that gives up
        on a slow `stop()` must not be left with a leaked socket (Task 3
        carry).

        A REJECTED login (`login()` completes and reports `valid=False` -
        the broker looked at these credentials and said no) is not a
        transient hiccup: it stops the daemon here, before either loop
        starts, and does not retry. Repeatedly reconnecting a trade socket
        or re-polling reads against credentials the broker just refused is
        exactly the behaviour that trips a real broker's rate-limiting /
        account-lock defenses. `status()`/`_last_error` carry the broker's
        own rejection reason; a restart with corrected credentials is the
        recovery path, not an internal retry loop.

        A TRANSIENT failure (the SOAP call itself failing to complete - a
        network error, a timeout - so `login()` never produces a `valid`
        verdict at all) is a different case: `login()` raises, and that
        exception propagates out of `run()` unhandled rather than being
        folded into the rejection path above, so a caller can tell "the
        broker said no" apart from "we couldn't ask it".
        """
        result = await self.login()
        if not result["valid"]:
            self._last_error = (
                f"login rejected: {result.get('raw') or 'invalid credentials'}"
            )
            log.error(self._last_error)
            return
        self._session.token = self._user_id
        # Reads come from the SOAP poll loop. The trade socket carries
        # order/trade events (a later phase, with an unresolved handshake), so
        # it is opened only when a transport was injected; with none, the
        # daemon is reads-only and never touches the socket.
        tasks = [asyncio.create_task(self._poll_loop())]
        if self._transport is not None:
            tasks.append(asyncio.create_task(self._run_socket()))
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        finally:
            await self._close_writer(self._current_writer)
            self._current_writer = None

    async def stop(self) -> None:
        """Signal shutdown and close the socket immediately. Deliberately
        does none of `run()`'s own awaiting of the poll/socket tasks - this
        must return fast even mid-poll or mid-handshake, so a supervisor
        with a timeout on `stop()` is never forced to abandon (and thereby
        leak) this daemon's socket (Task 3 carry)."""
        self._stop_requested.set()
        await self._close_writer(self._current_writer)
        self._current_writer = None
