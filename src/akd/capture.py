"""Read a `tcpdump` recording of the OFFICIAL TradeCast client and print a REDACTED report of its
messages, to learn the layout of an order message. Runs locally on your machine; sends nothing anywhere.

Why redaction matters: an order message carries your trading PIN. This tool never prints a value it
can't classify as harmless. Values are shown only when they are (a) the order you told it about
(symbol, side, quantity, price: shown with a label), (b) short protocol words such as message types
(`AUTHENTICATE_PARTIAL`, `REG`, `BUY`: an explicit allow-list, KNOWN_WORDS, never a pattern), or (c) tiny
numbers. Everything else is printed as a SHAPE only (`<digits:4>`, `<text:9>`, `<b64:88>`), so a 4-digit
PIN appears as `<digits:4>` and an unknown uppercase word such as a session token as `<text:N>`. The account
id, user code and any id-like SOAP value (learned from the messages) are masked wherever they appear, even
inside a longer value.
There is deliberately no option to print raw values.

The recording itself DOES contain the PIN. Keep it private and delete it after the report is made.

Supports classic pcap files (what `tcpdump -w` writes) over Ethernet, loopback, Linux cooked, raw IP and
macOS PKTAP (best effort). IPv4 only. TLS traffic is not readable (the AKD trade socket is not TLS).
"""
from __future__ import annotations

import argparse
import bisect
import re
import struct
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .protocol.codec import xor

TRADE_PORT = 8092
HTTP_PORT = 80
MAX_GAP = 1 << 20      # the most missing bytes a recording may hide before the stream is re-based
PCAP_MAGICS = {
    b"\xd4\xc3\xb2\xa1": ("<", 1e-6), b"\xa1\xb2\xc3\xd4": (">", 1e-6),      # microsecond, LE / BE
    b"\x4d\x3c\xb2\xa1": ("<", 1e-9), b"\xa1\xb2\x3c\x4d": (">", 1e-9),      # nanosecond, LE / BE
}
PCAPNG_MAGIC = b"\x0a\x0d\x0d\x0a"


class CaptureError(Exception):
    """The file can't be read as a capture; the message says what to do."""


@dataclass
class Packet:
    ts: float
    src: str
    sport: int
    dst: str
    dport: int
    seq: int
    flags: int
    payload: bytes


# ---- pcap ---------------------------------------------------------------------------------------
def _ip_payload(frame: bytes, linktype: int) -> bytes | None:
    """Strip the link layer. Returns the IPv4 packet bytes, or None if this frame isn't IPv4."""
    if linktype == 1:                                  # Ethernet
        if len(frame) < 14:
            return None
        ethertype, off = struct.unpack(">H", frame[12:14])[0], 14
        while ethertype == 0x8100 and len(frame) >= off + 4:   # VLAN tag
            ethertype, off = struct.unpack(">H", frame[off + 2:off + 4])[0], off + 4
        return frame[off:] if ethertype == 0x0800 else None
    if linktype in (0, 108):                           # BSD loopback: 4-byte family
        return frame[4:] if len(frame) > 4 else None
    if linktype == 113:                                # Linux cooked
        return frame[16:] if len(frame) > 16 and struct.unpack(">H", frame[14:16])[0] == 0x0800 else None
    if linktype in (101, 12, 14):                      # raw IP
        return frame
    if linktype == 149 and len(frame) > 12:            # macOS PKTAP: header, then an inner frame
        hlen, inner = struct.unpack("<I", frame[0:4])[0], struct.unpack("<I", frame[8:12])[0]
        return _ip_payload(frame[hlen:], inner) if inner != 149 else None
    raise CaptureError(f"unsupported capture link type {linktype}. Re-record on a normal interface "
                       "(for example `-i en0`), or tell the developer this number.")


def read_pcap(path: str) -> list[Packet]:
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] == PCAPNG_MAGIC:
        raise CaptureError("this is a pcapng file. Re-record with plain `tcpdump -w file.pcap` (classic pcap), "
                           "or convert it: `editcap -F pcap in.pcapng out.pcap`.")
    if len(data) < 24 or data[:4] not in PCAP_MAGICS:
        raise CaptureError("not a pcap capture file (bad header). Was it recorded with `tcpdump -w`?")
    end, unit = PCAP_MAGICS[data[:4]]
    linktype = struct.unpack(end + "I", data[20:24])[0]
    pos, out = 24, []
    while pos + 16 <= len(data):
        sec, frac, incl, _orig = struct.unpack(end + "IIII", data[pos:pos + 16])
        frame = data[pos + 16:pos + 16 + incl]
        pos += 16 + incl
        ip = _ip_payload(frame, linktype)
        if not ip or len(ip) < 20 or ip[0] >> 4 != 4 or ip[9] != 6:            # IPv4 + TCP only
            continue
        ihl = (ip[0] & 0x0F) * 4
        total = struct.unpack(">H", ip[2:4])[0]
        tcp = ip[ihl:total] if total >= ihl else ip[ihl:]
        if len(tcp) < 20:
            continue
        sport, dport, seq, _ack = struct.unpack(">HHII", tcp[:12])
        off = (tcp[12] >> 4) * 4
        out.append(Packet(sec + frac * unit, ".".join(map(str, ip[12:16])), sport, ".".join(map(str, ip[16:20])),
                          dport, seq, tcp[13], tcp[off:]))
    return out


# ---- TCP reassembly -----------------------------------------------------------------------------
@dataclass
class Stream:
    """One direction of one TCP connection, reassembled in order."""
    data: bytearray = field(default_factory=bytearray)
    offsets: list[int] = field(default_factory=list)      # where each segment starts in `data` (ascending)
    times: list[float] = field(default_factory=list)      # when each segment arrived, same order
    gaps: int = 0

    def ts_at(self, offset: int) -> float:
        return self.times[max(bisect.bisect_right(self.offsets, offset) - 1, 0)]


def reassemble(packets: list[Packet], server_port: int) -> dict[tuple, dict[str, Stream]]:
    """{(client ip, client port, server ip): {"c2s": Stream, "s2c": Stream}} for one server port.
    Handles out-of-order and retransmitted segments; a missing segment is counted as a gap."""
    flows: dict[tuple, dict[str, list[Packet]]] = defaultdict(lambda: {"c2s": [], "s2c": []})
    for p in packets:
        if p.dport == server_port:
            flows[(p.src, p.sport, p.dst)]["c2s"].append(p)
        elif p.sport == server_port:
            flows[(p.dst, p.dport, p.src)]["s2c"].append(p)
    out = {}
    for key, dirs in flows.items():
        out[key] = {}
        for d, segs in dirs.items():
            st = Stream()
            syn = [p.seq + 1 for p in segs if p.flags & 0x02]
            base = syn[0] if syn else min((p.seq for p in segs), default=0)
            for p in sorted((p for p in segs if p.payload), key=lambda p: (p.seq - base) & 0xFFFFFFFF):
                rel = (p.seq - base) & 0xFFFFFFFF
                if rel > len(st.data):
                    st.gaps += 1
                    if rel - len(st.data) > MAX_GAP:       # joined mid-connection or wrapped: never allocate gigabytes
                        base = (p.seq - len(st.data)) & 0xFFFFFFFF
                        rel = len(st.data)
                    else:
                        st.data.extend(b"\x00" * (rel - len(st.data)))         # keep offsets honest
                if rel + len(p.payload) > len(st.data):                        # skip pure retransmissions
                    st.offsets.append(len(st.data))
                    st.times.append(p.ts)
                    st.data.extend(p.payload[len(st.data) - rel:])
            out[key][d] = st
    return out


# ---- redaction ----------------------------------------------------------------------------------
# Words printed as they are. An explicit list, NOT a pattern: an uppercase token or account id matches any
# pattern. A new protocol word shows as `<text:N>` until someone adds it here after checking it is harmless.
KNOWN_WORDS = frozenset("""
MSG-CLIENT MSG-SERVER MSG_CLIENT HI_FROM_CLIENT HI_FROM_SERVER AUTHENTICATE_PARTIAL AUTHENTICATE_END NEW_CLIENT
NEW_CLIENT_RETRY LOCALIP WELCOME STORED_MESSAGE_LIST STORED_MESSAGE ORDER_NUM ORDER_SENT ORDER_VALIDATE
ORDER_VALIDATE_OK ORDERSENDTOMARKETFAIL OrderSendToMarketSuccess ORDER_EXE ORDER_EXE_ACK ORDER_EXE_LOG
ACCOUNT_ORDER_EXE ACCOUNT_ORDER_EXE_LOG ORDER_MST ORDER_CATS MSGWINDOW MSGWINDOW_DEL MARGINCALL EXPOSURE_LIMIT
COLLATERAL OPENPOSITION NEW_ORDER NEW_ORDER_SINGLE_MARKET NEW_ORDER_MARKET NEW_ORDER_BASCKET CHG_ORDER
CANCEL_ORDER_SINGLE CANCEL_ORDER_MULTI DISCONNECT_CLIENT ENDUP GET_USER_ACTIVE_ACCOUNTS ACCOUNT_SYN
TRD SLO MIT SHS NEW CFO CXL REG ODL FUT CSF SIF IPO BUY SEL LMT mkt TRUE FALSE
NOR ACP ACT FIL REJ QUE PEN APT ACV CLX CHG
OPN PRE SUS HALTED OHO OHP OHR OHC OHS PCL POSTCLOSED
ALL BOARD_MEETING HOUSE_MESSAGE
LIMIT DAY GTC IOC OK true false
""".split())
_IDENT = re.compile(r"^[A-Za-z][A-Za-z0-9]{2,60}$")        # a SOAP method name from the SOAPAction header
_SECRETISH = re.compile(r"user|id|token|session|account|other|pass|pin|code", re.I)   # SOAP field names to learn from
_DECIMAL = re.compile(r"^-?\d+\.\d+$")
_IP = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
_B64 = re.compile(r"^[A-Za-z0-9+/=]{16,}$")


@dataclass
class Redactor:
    symbol: str
    side: str
    qty: int
    price: float
    masked: set[str] = field(default_factory=set)

    def label(self, v: str) -> str | None:
        if v.upper() == self.symbol:
            return "SYMBOL"
        if v.upper() == self.side or (self.side == "SELL" and v.upper() == "SEL"):   # the wire word for a sell
            return "SIDE"
        try:
            if _DECIMAL.match(v) and abs(float(v) - self.price) < 1e-9:
                return "PRICE"
            if v.isdigit() and len(v) <= 3 and int(v) == self.qty:        # 4+ digits could be the PIN: never labelled
                return "QTY?"
        except ValueError:
            pass
        return None

    def shape(self, v: str, _label: bool = True) -> str:
        if v == "":
            return "<empty>"
        if "~" in v:                                     # a sub-list: never label its pieces (a "1" is not the quantity)
            return "~".join(self.shape(x, False) for x in v.split("~"))
        if ";" in v or "$" in v:                         # a record (e.g. NEW_ORDER): label each piece
            return ";".join("$".join(self.shape(y) for y in x.split("$")) for x in v.split(";"))
        if any(m in v for m in self.masked):             # by substring: a secret inside a longer value too
            return "<masked>"
        lab = self.label(v) if _label else None
        if lab:
            return f"{v}<{lab}>"
        if _IP.match(v):
            return "<ip>"
        if v.isdigit():
            return v if len(v) <= 3 else f"<digits:{len(v)}>"       # 4+ digits could be a PIN or an id
        if _DECIMAL.match(v):
            a, b = v.lstrip("-").split(".")
            return f"<decimal:{len(a)}.{len(b)}>"
        if v in KNOWN_WORDS:
            return v                                                 # protocol words: message types, market codes
        if _B64.match(v):
            return f"<b64:{len(v)}>"
        return f"<text:{len(v)}>"


# ---- decoding -----------------------------------------------------------------------------------
@dataclass
class Event:
    ts: float
    kind: str       # c2s | s2c | http-req | http-resp
    text: str
    mtype: str = ""
    labels: tuple[str, ...] = ()
    # RAW field values. Private to this module and WIPED as soon as the safe `text` is produced
    # (see build_report); never printed. repr=False keeps them out of any accidental debug output.
    fields: list[str] = field(default_factory=list, repr=False)


def socket_events(streams: dict[tuple, dict[str, Stream]]) -> list[Event]:
    """Decode every trade-socket message. Each Event carries its RAW fields until build_report has learned
    which values to mask and wiped them; they never leave this module."""
    events: list[Event] = []
    for dirs in streams.values():
        for d, st in dirs.items():
            text = xor(bytes(st.data)).decode("latin-1")
            start = 0
            while (end := text.find("*", start)) != -1:
                frame, off = text[start:end], start
                start = end + 1
                fields = frame.split("|")
                if fields and fields[-1] == "":
                    fields = fields[:-1]
                if len(fields) < 2:
                    continue
                events.append(Event(st.ts_at(off), d, "", fields[1], (), list(fields)))
    return sorted(events, key=lambda e: e.ts)


def _mask(red: Redactor, values) -> None:
    """Remember values to hide everywhere. Short values and harmless protocol words are skipped: masking
    them would blank unrelated text."""
    red.masked.update(x for x in values if x and len(x) >= 3 and x not in KNOWN_WORDS and not x.replace("~", "").isdigit())


ORDER_TYPES = ("NEW_ORDER", "NEW_ORDER_SINGLE_MARKET", "NEW_ORDER_MARKET", "NEW_ORDER_BASCKET", "CHG_ORDER", "ORDER_VALIDATE")


def learn_secrets(raw: list[list[str]], red: Redactor, account: str | None) -> None:
    """The account id and user code appear in the login and order messages; mask them everywhere."""
    if account:
        red.masked.add(account)
    for f in raw:
        if len(f) >= 4 and f[1] in ("AUTHENTICATE_PARTIAL", "NEW_CLIENT", "LOCALIP", "HI_FROM_CLIENT"):
            _mask(red, f[2:4])
        if len(f) >= 3 and f[1] in ORDER_TYPES:
            pieces = f[2].split(";")
            if len(pieces) > 11:                       # ...;account;userID;userCode;... (pieces 9, 10, 11)
                _mask(red, pieces[9:12])


def _soap_fields(text: str):
    """(name, value) pairs of every SOAP request body in an HTTP stream."""
    for m in re.finditer(r"(POST|GET) (\S+) HTTP/1\.\d\r\n(.*?)\r\n\r\n(.*?)(?=(?:POST|GET) \S+ HTTP/1|\Z)", text, re.S):
        yield m, re.findall(r"<(?:\w+:)?(\w+)[^>]*>([^<]*)</", m.group(4))


def learn_soap_secrets(streams: dict[tuple, dict[str, Stream]], red: Redactor) -> None:
    """Values of SOAP request fields named like an id, user, token, session or code are masked everywhere."""
    for dirs in streams.values():
        for st in dirs.values():
            for _m, fields in _soap_fields(bytes(st.data).decode("latin-1")):
                _mask(red, (v for k, v in fields if _SECRETISH.search(k)))


def http_events(streams: dict[tuple, dict[str, Stream]], red: Redactor) -> list[Event]:
    out: list[Event] = []
    for dirs in streams.values():
        for st in dirs.values():
            text = bytes(st.data).decode("latin-1")
            for m, fields in _soap_fields(text):
                head = m.group(3)
                action = re.search(r"SOAPAction:\s*\"?([^\"\r\n]+)", head, re.I)
                name = action.group(1).rsplit("/", 1)[-1] if action else ""
                # a method name from the header is a plain identifier; anything else (a URL path) is shaped,
                # and a query string is dropped before it is looked at
                path = m.group(2).split("?", 1)[0].split("#", 1)[0].rsplit("/", 1)[-1]
                op = name if _IDENT.match(name) and not any(x in name for x in red.masked) else red.shape(name or path)
                shaped = " ".join(f"{k}={red.shape(v)}" for k, v in fields[:12])
                out.append(Event(st.ts_at(m.start()), "http-req", f"{m.group(1)} {op}  {shaped}".rstrip(), op))
            for m in re.finditer(r"<return>(.*?)</return>", text, re.S):
                body = m.group(1)
                recs = body.split("|")
                shaped = " | ".join(";".join(red.shape(x) for x in r.split(";")[:8]) for r in recs[:3])
                out.append(Event(st.ts_at(m.start()), "http-resp", f"<return> {len(recs)} records: {shaped}", "return"))
    return sorted(out, key=lambda e: e.ts)


@dataclass
class Report:
    lines: list[str]


def build_report(packets: list[Packet], red: Redactor, account: str | None = None, full: bool = False) -> Report:
    if not packets:
        raise CaptureError("the capture has no TCP packets. Start tcpdump BEFORE opening TradeCast, and check the "
                           "interface (`-i en0`).")
    trade = reassemble(packets, TRADE_PORT)
    web = reassemble(packets, HTTP_PORT)
    if not trade:
        raise CaptureError(f"no traffic on the trade socket (port {TRADE_PORT}) in this capture. Was TradeCast running "
                           "and logged in while recording, and is the recording on the right interface?")
    events = socket_events(trade)
    learn_secrets([e.fields for e in events], red, account)
    learn_soap_secrets(web, red)
    for e in events:                                   # now that secrets are known, produce the safe text
        shaped = [red.shape(x) for x in e.fields]
        e.text = "|".join(shaped)
        e.mtype = red.shape(e.mtype)                   # the message type is a value too: never printed raw
        e.labels = tuple(m for s in shaped
                         for m in re.findall(r"[^;~$|<]*<(?:SYMBOL|SIDE|PRICE|QTY\?)>", s))
        e.fields = []                                  # raw values are gone from here on
    web_events = http_events(web, red)
    t0 = min(e.ts for e in [*events, *web_events]) if events or web_events else 0.0

    lines = ["AKD capture report (REDACTED: values you didn't tell me about are shown as shapes)", ""]
    span = max(p.ts for p in packets) - min(p.ts for p in packets)
    gaps = sum(s.gaps for d in trade.values() for s in d.values())
    lines += [f"{len(packets)} TCP packets over {span:.0f}s; trade-socket connections: {len(trade)}; "
              f"web-service connections: {len(web)}; missing-segment gaps: {gaps}", ""]

    census = Counter((e.kind, e.mtype) for e in events)
    lines.append("MESSAGE TYPES on the trade socket (count):")
    for (kind, mtype), n in sorted(census.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        lines.append(f"  {'client -> AKD' if kind == 'c2s' else 'AKD -> client'}  {mtype:<28} x{n}")
    lines.append("")

    cands = [i for i, e in enumerate(events) if e.kind == "c2s" and len({x.split("<")[-1] for x in e.labels}) >= 2]
    lines.append("ORDER CANDIDATES (client messages containing your order's symbol / side / qty / price):")
    if not cands:
        lines.append("  none found. Check the --symbol/--side/--qty/--price you passed match the order you placed exactly, "
                     "and that the recording was running when you placed it.")
    for i in cands:
        e = events[i]
        lines.append(f"  +{e.ts - t0:7.2f}s  {e.text}")
        lines.append(f"      labelled fields: {', '.join(e.labels)}")
        for nxt in events[i + 1:i + 6]:
            if nxt.kind == "s2c" and nxt.ts - e.ts < 10:
                lines.append(f"      reply +{nxt.ts - e.ts:5.2f}s  {nxt.text}")
    lines.append("")
    pinlike = sum(1 for e in events if e.kind == "c2s" and "<digits:4>" in e.text)
    if pinlike:
        lines.append(f"NOTE: {pinlike} client message(s) contain a 4-digit value shown only as <digits:4> (possibly the PIN).")
        lines.append("")

    lines.append("WEB-SERVICE (SOAP) operations seen:")
    for op, n in sorted(Counter(e.mtype for e in web_events if e.kind == "http-req").items()):
        lines.append(f"  {op:<32} x{n}")
    lines.append("")
    show = events if full else [e for e in events if e.kind == "s2c" or e.mtype not in ("HI_FROM_CLIENT",)]
    lines.append("TIMELINE (trade socket; heartbeats hidden unless --full):")
    for e in show[:400]:
        arrow = "->" if e.kind == "c2s" else "<-"
        lines.append(f"  +{e.ts - t0:7.2f}s {arrow} {e.text[:220]}")
    if len(show) > 400:
        lines.append(f"  ... {len(show) - 400} more")
    lines += ["", "WEB-SERVICE timeline:"]
    lines += [f"  +{e.ts - t0:7.2f}s {e.kind:<9} {e.text[:220]}" for e in web_events[:120]]
    return Report(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m akd.capture", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report", help="decode a tcpdump recording into a redacted report")
    r.add_argument("pcap")
    r.add_argument("--symbol", required=True, help="the symbol of the order you placed, e.g. OGDC")
    r.add_argument("--side", required=True, choices=["BUY", "SELL", "buy", "sell"])
    r.add_argument("--qty", required=True, type=int)
    r.add_argument("--price", required=True, type=float, help="the limit price you used, e.g. 150.00")
    r.add_argument("--account", help="your AKD account id, to mask it everywhere")
    r.add_argument("--full", action="store_true", help="include heartbeats")
    args = ap.parse_args(argv)
    try:
        packets = read_pcap(args.pcap)
        red = Redactor(args.symbol.upper(), args.side.upper(), args.qty, args.price)
        report = build_report(packets, red, args.account, args.full)
    except CaptureError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print("\n".join(report.lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
