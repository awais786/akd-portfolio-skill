"""AKD SOAP login and read operations (protocol map §1-5).

`login()` makes one SOAP login; `poll_once()` runs each read operation once and writes the decoded
results to the snapshot store that `reads.py` turns into answers. The password comes from the injected
credentials provider (or `AKD_PASSWORD` by default), is never logged, and is never a parameter.

All network is injectable (`http`) so the whole daemon is testable with NO live broker connection.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from .config import SERVER_CODE, SOAP_URL
from .credentials import EnvCredentials
from .protocol import soap
from .protocol.crypto import load_public_key
from .protocol.decode import (
    decode_collateral,
    decode_exposure,
    decode_order_history,
    maybe_decompress,
)
from .store import SnapshotStore

__all__ = ["AkdDaemon"]

log = logging.getLogger("akd.daemon")

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
    """One AKD account's SOAP login and read polling.

    `http` is an `httpx.AsyncClient`-shaped object (`async def post(url, *, content, headers)`), the
    one injectable network seam.
    """

    def __init__(
        self,
        *,
        account: str,
        store: SnapshotStore,
        http=None,
        creds=None,
    ) -> None:
        self.account = account
        self._store = store
        self._http = http
        # Whose AKD login to use: `portfolio.py` injects the env-supplied one; the default reads AKD_PASSWORD.
        self._creds = creds or EnvCredentials()
        self._public_key = load_public_key(_KEY)
        self._authenticated = False
        self._last_error: str | None = None

    # -- SOAP: login + reads -------------------------------------------------

    async def login(self) -> dict:
        """POST `SecurecustomAuthentication` and record the result.

        The password is read from the credentials provider here - never accepted as a parameter, never
        included in any log line. The parsed `valid` flag is what sets `self._authenticated`.
        """
        password = self._creds.password()
        if not password:
            # No credentials: report a clean rejection instead of crashing on a KeyError.
            parsed = {"valid": False, "token": None,
                      "raw": "no AKD credentials (set AKD_USER_ID and AKD_PASSWORD)"}
            self._authenticated = False
            return parsed
        login_user = self._creds.login_user_id(self.account)
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
        # A VALID response carries the live session token (`true|<token>|...`), so it
        # is never logged. A rejected one is the broker's own failure text, which is
        # what makes a failure legible, so that is kept (truncated).
        redacted = "<redacted: valid login carries a session token>"
        diag = {
            "url": SOAP_URL, "user": login_user, "server_code": SERVER_CODE,
            "password_len": len(password), "positions": positions,
            "request_bytes": len(body), "http_status": status,
            "response_len": len(resp.text),
            "response_body": redacted if parsed["valid"] else resp.text[:800],
            "parsed_valid": parsed["valid"],
            "parsed_raw": redacted if parsed["valid"] else parsed.get("raw"),
        }
        log.info("AKD login diagnostic: %s", diag)
        self._authenticated = parsed["valid"]
        return parsed

    def _soap_account(self) -> str:
        """The AKD account for SOAP calls (terminal-set), distinct from
        `self.account` which keys the local snapshot store."""
        return self._creds.login_user_id(self.account)

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
                {
                    "symbol": p.symbol, "quantity": p.quantity, "avg_cost": p.avg_cost,
                    "market_price": p.market_price, "market_value": p.market_value,
                    "settled_pl": p.settled_pl, "unsettled_pl": p.unsettled_pl,
                }
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

    # -- lifecycle ---------------------------------------------------------

    def status(self) -> dict:
        """What is and isn't confirmed. `authenticated` reflects only the SOAP login's `valid` flag."""
        return {
            "account": self.account,
            "authenticated": self._authenticated,
            "last_error": self._last_error,
            "capabilities": ["reads"],
        }
