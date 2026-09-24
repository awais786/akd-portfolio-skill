"""Trade-socket session: NEW_CLIENT -> AUTHENTICATE_PARTIAL -> HI_FROM_CLIENT
loop (protocol map §3-4).

Pure framing plus a byte-accumulating, explicitly-stated state machine. The
actual socket I/O (opening the connection, reading off the wire, writing the
frames this module builds, running the heartbeat on a timer) lives in the
daemon (Task 12) - nothing here touches a socket, so it is fully testable by
feeding it bytes.

The AUTHENTICATE_PARTIAL position tail is always a caller-supplied parameter,
never derived here - its `LoginForm2` derivation is the one live-only unknown
(spec §15, criterion 7; see also Task 8/9's redaction convention).

Handshake acknowledgement content is ALSO an undocumented unknown: the
protocol map (docs/akd-protocol/2026-09-20-tradecast-protocol-map.md §3)
enumerates only the client->server message types seen in capture - no
server->client ack was captured. `handshake_step` therefore does not assume
any specific ack payload; it only requires a well-formed MSG-CLIENT-prefixed
reply (the one invariant the codec itself guarantees on the wire) to advance,
and treats anything else - no reply at all, or a malformed one - as a reason
to NOT advance. This keeps the state machine honest: it never fabricates
"authenticated" from silence or garbage.
"""

from __future__ import annotations

from .codec import encode_message, iter_messages

_MSG_CLIENT = "MSG-CLIENT"


def build_new_client(account: str, local_ip: str) -> bytes:
    """The `NEW_CLIENT` frame sent first on every connect/reconnect."""
    return encode_message(["NEW_CLIENT", account, local_ip])


def build_authenticate_partial(account: str, token: str, position_tail: str) -> bytes:
    """`MSG-CLIENT|AUTHENTICATE_PARTIAL|<account>|<token>|<p1~p2~p3~p4>|*`.

    `position_tail` (e.g. "1~2~3~4") is supplied by the caller - its
    derivation from `LoginForm2` is the one live/manual unknown in this
    plan, so this builder takes it as a parameter and never invents it.
    """
    return encode_message(["AUTHENTICATE_PARTIAL", account, token, position_tail])


def build_heartbeat(account: str) -> bytes:
    """The periodic `HI_FROM_CLIENT` keepalive frame."""
    return encode_message(["HI_FROM_CLIENT", account])


class SessionState:
    """Trade-socket session state machine.

    States: ``connecting`` -> ``authenticating`` -> ``live`` -> (on a lost
    connection or a bad/absent handshake reply) ``reconnecting`` -> back to
    ``authenticating``. This is a real state field, not an optimistic
    "authenticated" flag: the session can only ever be ``live`` after it has
    actually driven the handshake to completion, and a dropped connection or
    a bad reply explicitly moves it back out of ``live`` rather than leaving
    a stale True behind.
    """

    def __init__(
        self,
        account: str,
        local_ip: str = "",
        token: str = "",
        position_tail: str = "",
    ) -> None:
        self.account = account
        self.local_ip = local_ip
        self.token = token
        self.position_tail = position_tail
        self._buf = b""
        self.state = "connecting"
        self._authenticate_sent = False

    def feed(self, buffer: bytes) -> list[list[str]]:
        """Accumulate wire bytes; return newly-complete messages.

        The trailing incomplete frame is carried in ``self._buf`` across
        calls (via `iter_messages`' remainder), so a frame split by a short
        read - anywhere in the frame, not just near a boundary - is
        reassembled exactly once and never reported as a half-frame. Two
        frames landing in a single read are both returned, in order.
        """
        self._buf += buffer
        messages, self._buf = iter_messages(self._buf)
        return messages

    def handshake_step(self, messages: list[list[str]]) -> bytes | None:
        """Advance the handshake given the newest batch of decoded inbound
        messages (as returned by `feed`), and return the next frame to send,
        or ``None`` if there is nothing to send right now.

        - ``connecting`` / ``reconnecting`` -> sends `NEW_CLIENT`, moves to
          ``authenticating``.
        - ``authenticating``, no reply yet (``messages`` empty) -> stays put;
          an absent response never advances the state.
        - ``authenticating``, a well-formed reply arrives -> sends
          `AUTHENTICATE_PARTIAL` (first reply) or moves to ``live`` (second
          reply); nothing further is sent once live.
        - ``authenticating``, a malformed reply arrives (does not carry the
          codec's own `MSG-CLIENT` prefix) -> a wrong response is treated the
          same as a broken connection: falls back to ``reconnecting`` rather
          than forging ahead into ``live``.
        - ``live`` -> a no-op; heartbeats are sent separately via
          `build_heartbeat` on the daemon's own timer, not through this
          method.
        """
        if self.state in ("connecting", "reconnecting"):
            self.state = "authenticating"
            self._authenticate_sent = False
            return build_new_client(self.account, self.local_ip)

        if self.state != "authenticating":
            return None  # live: handshake already done, nothing to send

        if not messages:
            return None  # absent reply - do not advance

        if not self._is_well_formed(messages[-1]):
            # wrong reply - do not silently proceed to live
            self.state = "reconnecting"
            return None

        if not self._authenticate_sent:
            self._authenticate_sent = True
            return build_authenticate_partial(
                self.account, self.token, self.position_tail
            )

        self.state = "live"
        return None

    def reset(self) -> None:
        """Called by the daemon when the real connection drops out from under
        an established session. Forces the state back to ``reconnecting`` and
        discards any buffered partial frame - it belonged to the dead
        connection and must not be spliced onto bytes from a new one."""
        self.state = "reconnecting"
        self._authenticate_sent = False
        self._buf = b""

    @staticmethod
    def _is_well_formed(message: list[str]) -> bool:
        return bool(message) and message[0] == _MSG_CLIENT
