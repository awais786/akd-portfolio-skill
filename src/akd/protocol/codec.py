"""The XOR-0x1D transform of the AKD sockets (protocol map §3). Pure: bytes in, bytes out.

Every byte on the wire (both directions) is XOR 0x1D. Decoded, a frame is `MSG-CLIENT|<field>|<field>|...*`:
'|' separates fields, '*' terminates a message, and '~' is a sub-list delimiter inside a field.
There is deliberately NO encoder and no framing helper: nothing in this project sends anything to AKD
(CLAUDE.md rule 1), and the one reader of these frames (`capture.py`) needs byte offsets for its timeline.
"""

from __future__ import annotations

_TABLE = bytes(i ^ 0x1D for i in range(256))


def xor(data: bytes) -> bytes:
    """The involutive `b ^ 0x1D` transform: encode and decode are the same."""
    return data.translate(_TABLE)
