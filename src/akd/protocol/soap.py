"""SOAP envelope handling for TradeCast's web services: pulling the
`<return>` body out of a captured response, and composing the request
envelopes this plugin sends.

`extract_return` is pure text in, text out - no I/O, no network - and was
originally read-only by design: captured traffic was replayed byte-for-byte
and nothing composed a TradeCast request. That changed here: the direct-
connect daemon (Task 12) needs to actually send GetCollateral,
GetOrderHistory, GetAccountStatement and the SecurecustomAuthentication
login, so the builders below compose those envelopes in the same shape the
captures show. Still no I/O in this module - callers own the socket/HTTP
transport.
"""
from __future__ import annotations

import random
import re

from .crypto import encrypt_login_field

_RETURN = re.compile(r"<return>(.*?)</return>", re.DOTALL)


def extract_return(xml: str) -> str | None:
    """Pull the `<return>...</return>` body out of a SOAP response.

    Every TradeCast web-service response wraps its payload in a single
    `<return>` element regardless of operation (GetCollateral,
    GetOrderHistory, ...) - this is the one place that unwrapping happens.
    Returns None when the element is missing, never raises.
    """
    m = _RETURN.search(xml)
    return m.group(1) if m else None


_ENVELOPE = (
    '<?xml version="1.0" ?>'
    '<S:Envelope xmlns:S="http://schemas.xmlsoap.org/soap/envelope/"><S:Body>'
    '<ns2:{op} xmlns:ns2="http://login/">{inner}</ns2:{op}>'
    "</S:Body></S:Envelope>"
)


def _account_body(op: str, user_id: str, account: str) -> str:
    inner = f"<UserID>{user_id}</UserID><Account>{account}</Account>"
    return _ENVELOPE.format(op=op, inner=inner)


def build_getcollateral(user_id: str, account: str) -> str:
    """The `GetCollateral` request body: holdings + margin snapshot."""
    return _account_body("GetCollateral", user_id, account)


def build_getorderhistory(user_id: str, account: str) -> str:
    """The `GetOrderHistory` request body: executed-fills history."""
    return _account_body("GetOrderHistory", user_id, account)


def build_getexposuredynamic(account: str) -> str:
    """`GetExposureDynamic` request: cash / exposure / free-cash / share worth.
    Field shape recovered from a captured request (UserID + account + approved)."""
    inner = (f"<UserID>{account}</UserID><account>{account}</account>"
             "<approved>MultipleTab</approved>")
    return _ENVELOPE.format(op="GetExposureDynamic", inner=inner)


def build_getaccountstatement(user_id: str, account: str) -> str:
    """The `GetAccountStatement` request body: cash ledger."""
    return _account_body("GetAccountStatement", user_id, account)


# AKD login is a partial (positional) password scheme, recovered from
# windows.LoginForm2 in TradeCast.jar and confirmed against a captured working
# request. The client picks 4 distinct random character positions in [1,7]
# (LoginForm2.GetRandomNumber: `new Random().nextInt(7) + 1`, retried until
# distinct), sends the password characters AT those positions as `Password`,
# and the positions themselves pipe-joined as `PasswordPosition`. The full
# password never goes on the wire; only its first 7 characters are ever used.
LOGIN_POSITION_COUNT = 4
LOGIN_POSITION_SPAN = 7


def partial_password_fields(password: str, positions: list[int]) -> tuple[str, str]:
    """`(Password, PasswordPosition)` for a positional login.

    `positions` are 1-based character positions. The returned partial password
    is those characters IN THE SAME ORDER as `positions`, and the position
    field is the same positions pipe-joined - the server matches them
    index-for-index, so the two must stay aligned.
    """
    partial = "".join(password[p - 1] for p in positions)
    position_field = "|".join(str(p) for p in positions)
    return partial, position_field


def choose_login_positions(
    password_len: int,
    *,
    count: int = LOGIN_POSITION_COUNT,
    span: int = LOGIN_POSITION_SPAN,
    rng: random.Random | None = None,
) -> list[int]:
    """Pick `count` distinct 1-based positions in [1, min(span, password_len)].

    Mirrors LoginForm2's distinct random choice; capped at the password length
    so a short password is never indexed past its end.
    """
    # Not a security decision: the positions are declared to the server in the
    # PasswordPosition field, not a secret, and the TradeCast client itself uses
    # java.util.Random for exactly this. Non-crypto RNG matches the protocol.
    rng = rng or random.Random()
    hi = min(span, password_len)
    return rng.sample(range(1, hi + 1), min(count, hi))


def build_secure_auth(
    username: str,
    password: str,
    password_position: str,
    public_key,
    *,
    server_code: str,
) -> str:
    """The `SecurecustomAuthentication` login request body.

    All four fields (UserName, Password, PasswordPosition, ServerCode) are
    RSA/PKCS1v15-encrypted with `encrypt_login_field` before going on the
    wire - matching CATALYST.Security.Encrypter.rsaEncrypt (protocol map
    §2), never sent as plaintext.
    """
    fields = {
        "UserName": encrypt_login_field(username, public_key),
        "Password": encrypt_login_field(password, public_key),
        "PasswordPosition": encrypt_login_field(password_position, public_key),
        "ServerCode": encrypt_login_field(server_code, public_key),
    }
    inner = "".join(f"<{k}>{v}</{k}>" for k, v in fields.items())
    return _ENVELOPE.format(op="SecurecustomAuthentication", inner=inner)


def parse_auth_response(xml: str) -> dict:
    """Parse a `SecurecustomAuthentication` response's `<return>` body.

    The body is a `|`-joined record: `valid|token|role|...|greeting|...`.
    `valid` is True only when the first field is the literal "true"
    (case-insensitive) - anything else (a login failure message, a missing
    `<return>` element) parses to `valid=False` rather than raising, so a
    caller can always inspect `out["raw"]` for the broker's own message.
    """
    body = extract_return(xml) or ""
    parts = body.split("|")
    valid = bool(parts) and parts[0].strip().lower() == "true"
    return {
        "valid": valid,
        "token": parts[1] if len(parts) > 1 else None,
        "role": parts[2] if len(parts) > 2 else None,
        "raw": body,
    }
