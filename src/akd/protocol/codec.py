"""XOR-0x1D socket line codec + MSG-CLIENT framing (protocol map §3).

Pure: bytes in, structures out. No network, no file I/O.

Every byte on the wire (both directions) is XOR 0x1D. Decoded, a frame is
`MSG-CLIENT|<field>|<field>|...*` - '|' separates fields, '*' terminates a
message, and '~' is a sub-list delimiter *within* a field (e.g. a
`~`-joined positional tail) that the caller splits when a field's semantics
call for it; this codec never interprets '~'.

MyTcp.FormatMessage (the real client) carries no in-band escape for '|' or
'*'. A field value that itself contains one of those framing delimiters
cannot be represented on the wire without corrupting the split on decode, so
`encode_message` refuses to build such a frame rather than silently emit one
that would mis-split. '~' is not a framing delimiter, so it is not
restricted - fields carrying it round-trip unchanged.
"""

from __future__ import annotations

from collections.abc import Iterable

_XOR = 0x1D
_FIELD = "|"
_TERM = "*"
_PREFIX = "MSG-CLIENT"


def xor(data: bytes) -> bytes:
    """The involutive `b ^ 0x1D` transform - encode and decode are the same."""
    return bytes(b ^ _XOR for b in data)


def encode_message(fields: Iterable[str]) -> bytes:
    """Build `MSG-CLIENT|<f0>|<f1>|...*`, then XOR-encode it for the wire.

    Raises ValueError if any field contains '|' or '*': those are the framing
    delimiters, and the real client has no way to escape them either - a raw
    one in a field value is out of contract, not a case this codec can frame
    safely.
    """
    fields = list(fields)
    for field in fields:
        if _FIELD in field or _TERM in field:
            raise ValueError(
                f"field {field!r} contains a framing delimiter ('|' or '*'); "
                "MyTcp has no in-band escape for it, so it cannot be framed"
            )
    line = _FIELD.join((_PREFIX, *fields)) + _FIELD + _TERM
    return xor(line.encode("utf-8"))


def iter_messages(buffer: bytes) -> tuple[list[list[str]], bytes]:
    """XOR-decode a wire buffer and split it into complete MSG-CLIENT frames.

    Returns `(messages, rest)`: `messages` is each complete frame (terminated
    by '*'), split on '|', with the leading 'MSG-CLIENT' field kept in place
    at index 0. `rest` is the XOR-encoded tail after the last '*' - bytes of
    an incomplete frame that a caller should prepend to the next read rather
    than drop or replay (Review Focus 3).

    '~' sub-lists inside a field are left as raw strings; splitting them is
    the caller's job once it knows a given field is a '~'-joined list.
    """
    text = xor(buffer).decode("utf-8", errors="replace")
    *complete_frames, trailing = text.split(_TERM)

    messages: list[list[str]] = []
    for frame in complete_frames:
        if not frame:
            continue
        fields = frame.split(_FIELD)
        if fields and fields[-1] == "":
            fields = fields[:-1]  # drop the empty field from a trailing '|'
        messages.append(fields)

    rest = xor(trailing.encode("utf-8"))
    return messages, rest
