"""Build synthetic tcpdump files (classic pcap) for tests: Ethernet / IPv4 / TCP with chosen payloads.
The 'order' message in these tests uses an INVENTED layout: it exists only to exercise the tool."""
from __future__ import annotations

import socket
import struct

from akd.protocol.codec import xor

CLIENT = ("192.168.1.5", 50000)
SERVER = ("10.0.0.9", 8092)
WEB = ("10.0.0.9", 80)
SYN, PSH_ACK = 0x02, 0x18


def frame(linktype: int, src, dst, seq: int, flags: int, payload: bytes) -> bytes:
    tcp = struct.pack(">HHIIBBHHH", src[1], dst[1], seq, 0, 5 << 4, flags, 65535, 0, 0) + payload
    ip = struct.pack(">BBHHHBBH4s4s", 0x45, 0, 20 + len(tcp), 0, 0, 64, 6, 0,
                     socket.inet_aton(src[0]), socket.inet_aton(dst[0])) + tcp
    if linktype == 1:
        return b"\x00" * 12 + b"\x08\x00" + ip
    if linktype == 0:
        return b"\x02\x00\x00\x00" + ip
    if linktype == 113:
        return b"\x00" * 14 + b"\x08\x00" + ip
    return ip                                           # 101: raw


def pcap(packets, *, linktype=1, magic="le-us") -> bytes:
    """packets: list of (ts, src, dst, seq, flags, payload)."""
    end, mg, unit = {"le-us": ("<", b"\xd4\xc3\xb2\xa1", 1e6), "be-us": (">", b"\xa1\xb2\xc3\xd4", 1e6),
                     "le-ns": ("<", b"\x4d\x3c\xb2\xa1", 1e9)}[magic]
    out = mg + struct.pack(end + "HHiIII", 2, 4, 0, 0, 65535, linktype)
    for ts, src, dst, seq, flags, payload in packets:
        fr = frame(linktype, src, dst, seq, flags, payload)
        sec = int(ts)
        out += struct.pack(end + "IIII", sec, int(round((ts - sec) * unit)), len(fr), len(fr)) + fr
    return out


def wire(*messages: list[str]) -> bytes:
    """Client->server bytes: each message framed and XOR-encoded like the real client."""
    return b"".join(xor(("|".join(m) + "|*").encode()) for m in messages)


ACCOUNT = "COAF00000"
TOKEN = "AbC123xyzToken"
PIN = "4321"
ORDER = ["MSG-CLIENT", "NEW_ORDER", ACCOUNT, "REG", "OGDC", "BUY", "LIMIT", "1", "150.00", PIN, "DAY"]


def session_packets(t0=1000.0):
    """A whole (invented) session: handshake, heartbeat, an order, the server's replies."""
    c2s = [
        ["MSG-CLIENT", "NEW_CLIENT", ACCOUNT, "192.168.1.5"],
        ["MSG-CLIENT", "AUTHENTICATE_PARTIAL", ACCOUNT, TOKEN, "1~2~3~4"],
        ["MSG-CLIENT", "HI_FROM_CLIENT", ACCOUNT],
        ORDER,
    ]
    s2c = [
        ["MSG-SERVER", "AUTHENTICATE_END", "true"],
        ["MSG-SERVER", "ORDER_SENT", "123456789", "OK"],
    ]
    return c2s, s2c
