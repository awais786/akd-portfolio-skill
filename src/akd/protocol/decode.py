"""Pure decoders for AKD/TradeCast SOAP payloads.

Extracted from the ingest script that used to parse these exact wire formats
against the live broker directly - this module changes where the logic
lives, not what it does. Every decoder here is a pure function:
text in (plus an explicit `as_of`), typed models out. No filesystem, no
network, no clock - `as_of` is always a parameter, because a decoder that
reads the clock cannot be tested deterministically and a position with no
capture time is a claim with no expiry.

A malformed record is skipped, never raised: one bad row must not lose the
whole book.
"""

from __future__ import annotations

import base64
import gzip
import re
from datetime import UTC, date, datetime

from .models import AkdOrder, AkdPosition
from .soap import extract_return

__all__ = [
    "decode_collateral",
    "decode_order_history",
    "extract_return",
    "maybe_decompress",
]

# --- GetCollateral --------------------------------------------------------
#
# Layout (header row first, records "|"-joined, fields ";"-joined):
#   Symbol;Quantity;TotalQty;Avg. BUY RATE;Sold Quantity;Avg. SELL RATE;...
# A capture always carries this header, so the header's own field order is
# used when present (exactly as the original ingest script's parse_collateral
# did). The defaults below apply ONLY to headerless input (the brief's own malformed-row
# test has none) and mirror the header's real, currently-observed column
# positions - see decode_collateral's docstring for why a present-but-wrong
# header fails closed instead of falling back to these.
_QTY_HEADER = "Quantity"
_COST_HEADER = "Avg. BUY RATE"
_DEFAULT_QTY_INDEX = 1
_DEFAULT_COST_INDEX = 3


def _records(text: str) -> list[str]:
    """Split a capture body into records, tolerating either the real "|"
    separator or newlines (a comment-marked or hand-built fixture).

    Comment/blank LINES are dropped first, before any "|" splitting - a
    "# ..." header comment (mandatory on a redacted fixture) is prose and may
    itself contain a "|" character; splitting on "|" first would fragment
    that prose into bogus pseudo-records once the leading "#" no longer
    covers the fragment after the split.
    """
    lines = [
        ln.strip()
        for ln in text.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    out: list[str] = []
    for line in lines:
        out.extend(part.strip() for part in line.split("|") if part.strip())
    return out


def _to_float(raw: str) -> float | None:
    raw = raw.strip()
    if not raw:
        return None
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


def _looks_like_header(fields: list[str]) -> bool:
    """A record is the header row if it names "Symbol", or if the field at
    the default quantity position isn't itself a number - i.e. it reads like
    a column name, not a holding. Used only to decide whether to trust the
    record's own column order or fall back to documented defaults; it is
    deliberately permissive so a genuine data row is never mistaken for one
    (the brief's headerless malformed-row fixture has a numeric field there
    and is correctly treated as data)."""
    if "Symbol" in fields:
        return True
    return (
        len(fields) > _DEFAULT_QTY_INDEX
        and _to_float(fields[_DEFAULT_QTY_INDEX]) is None
    )


def decode_collateral(text: str, as_of: datetime) -> list[AkdPosition]:
    """Decode a GetCollateral `<return>` body into long positions.

    Only rows with a positive quantity are holdings - a zero or short row is
    not a position to mirror. A row missing its symbol or quantity is
    skipped, never fatal to the rest of the book.

    DELIBERATE divergence from the original ingest script's parse_collateral:
    that parser decodes quantity and cost in one try/except, so a row with a garbled
    `Avg. BUY RATE` disappears from the book entirely. Here, a row with a
    missing or unparsable cost field still survives with `avg_cost=None` -
    the user still owns those shares even when the broker's cost column is
    garbled, and an accurate holding with no cost basis is a far better
    failure than a real position silently vanishing ("empty book reads as
    you own nothing", the failure mode this whole package exists to avoid).

    Column positions come from the header when one is present - REQUIRED to
    carry both `Quantity` and `Avg. BUY RATE`, or this raises rather than
    guess: on a header AKD has renamed or reordered, silently attaching the
    wrong column as cost basis is worse than no cost basis, because a wrong
    number looks plausible where a missing one doesn't. Only genuinely
    headerless input (no row that reads as a header at all) falls back to
    the defaults above, matching today's real column order - that path
    exists because the brief's own malformed-row test exercises it.
    """
    records = _records(text)
    if not records:
        return []

    header_fields = [h.strip() for h in records[0].split(";")]
    if _looks_like_header(header_fields):
        if _QTY_HEADER not in header_fields:
            raise ValueError(
                f"GetCollateral header missing {_QTY_HEADER!r} column: {records[0]!r}"
            )
        if _COST_HEADER not in header_fields:
            raise ValueError(
                f"GetCollateral header missing {_COST_HEADER!r} column: {records[0]!r}"
            )
        qty_index = header_fields.index(_QTY_HEADER)
        cost_index = header_fields.index(_COST_HEADER)
        data_records = records[1:]
    else:
        # No header at all - decode on the documented default positions
        # rather than refuse a capture that (unusually) omits its header.
        qty_index, cost_index = _DEFAULT_QTY_INDEX, _DEFAULT_COST_INDEX
        data_records = records

    out: dict[str, AkdPosition] = {}
    for record in data_records:
        cols = record.split(";")
        if not cols or not cols[0].strip() or len(cols) <= qty_index:
            continue
        symbol = cols[0].strip().upper()
        quantity = _to_float(cols[qty_index])
        if quantity is None or quantity <= 0:
            continue
        avg_cost = _to_float(cols[cost_index]) if len(cols) > cost_index else None
        out[symbol] = AkdPosition(
            symbol=symbol,
            # AkdPosition.quantity is declared int - a fractional quantity
            # (none seen in real captures) would be truncated here, not
            # rounded or preserved; this does not round-trip.
            quantity=int(quantity),
            avg_cost=avg_cost,
            as_of=as_of,
        )
    return list(out.values())


# --- GetOrderHistory -------------------------------------------------------
#
# Wire format (pipe-joined records, semicolon fields), already decompressed
# to plaintext by the time it reaches here:
#   SYMBOL;QTY;PRICE;VALUE;SIDE;SETTLEMENT;TRADE_DATE;SETTLE_DATE;
# e.g. MUGHAL;532;86.65;46179.99;BUY;T+1REG;Jun 15, 2026;Jun 16, 2026;
# Same field layout psx_trading.akd.parse.parse_orderhistory already knows
# for the CSV-pasted version of this data. TradeCast's own history carries no
# broker order id in this row - order_id is synthesised deterministically
# from the row's own CONTENT (symbol, trade date, and a sort over the other
# fields of same-day same-symbol fills), never from wall-clock, randomness or
# the position a record happens to occupy in the input: Task 12 content-hashes
# these payloads, and an id derived from iteration order would make
# reordered-but-identical input look like a change (or hide a real one).
#
# LIMITATION: unlike decode_collateral (verified 11/11 positions against a
# real GetCollateral capture), this has never been checked against GROUND
# TRUTH - the account's actual executed fills. A real GetOrderHistory
# capture now exists among this machine's captures and decodes cleanly (24
# fills, stored by apps/ingest/src/ingest/akd_worker.py without error), so
# this is no longer purely synthetic-fixture territory - but "decodes
# without error" is not the same claim as "decodes correctly", and nothing
# has yet compared its output against the broker's own record of what
# filled. It is otherwise tested only against synthetic input built from the
# field layout already documented in psx_trading.akd.parse.parse_orderhistory
# and the original ingest script. Do not assume the same real-capture parity
# decode_collateral has until ground truth confirms it.

_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9]{1,11}$")
_SIDES = ("BUY", "SELL")
_DATE_FORMATS = ("%b %d, %Y", "%b %d %Y", "%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y")


def _parse_trade_date(raw: str) -> date | None:
    raw = raw.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def maybe_decompress(text: str) -> str:
    """GetOrderHistory ships base64+gzip; GetCollateral does not. Try the
    decode and fall back to the raw text untouched - exactly what the
    original ingest script's response lookup did.

    Public (not `_`-prefixed) so a caller that needs to inspect the
    DECODED text before calling `decode_order_history` - e.g. checking for
    a "No Record" sentinel, which AKD may or may not send gzipped - can run
    the identical decompression rather than approximate it. Idempotent on
    already-decompressed text: gzip.decompress on plaintext fails and falls
    through to returning the text unchanged, so calling this twice (once by
    a caller, once inside decode_order_history) is always safe.
    """
    try:
        padded = text + "=" * (-len(text) % 4)
        return gzip.decompress(base64.b64decode(padded)).decode()
    except Exception:  # noqa: BLE001 - any failure means "this was plaintext"
        return text


def _order_content_key(
    cols: list[str], quantity: float, price: float, side: str
) -> tuple:
    """Deterministic sort key for same-(symbol, trade_date) fills, built from
    the record's own fields rather than its position in the input - see the
    module comment above decode_order_history for why."""
    tail = tuple(cols[3:]) if len(cols) > 3 else ()
    return (quantity, price, side, tail)


def decode_order_history(text: str, as_of: datetime) -> list[AkdOrder]:
    """Decode a GetOrderHistory `<return>` body into dated fills."""
    payload = maybe_decompress(text)
    records = _records(payload)

    parsed: list[tuple[str, date, float, float, str, list[str]]] = []
    for record in records:
        cols = [c.strip() for c in record.split(";")]
        if len(cols) < 7:
            continue
        symbol = cols[0].upper()
        if not _SYMBOL_RE.match(symbol):
            continue
        side = cols[4].upper()
        if side not in _SIDES:
            continue
        quantity = _to_float(cols[1])
        price = _to_float(cols[2])
        traded_on = _parse_trade_date(cols[6])
        if quantity is None or quantity <= 0 or price is None or traded_on is None:
            continue
        parsed.append((symbol, traded_on, quantity, price, side, cols))

    groups: dict[tuple[str, date], list[tuple]] = {}
    for item in parsed:
        groups.setdefault((item[0], item[1]), []).append(item)

    out: list[AkdOrder] = []
    for (symbol, traded_on), items in groups.items():
        ordered = sorted(
            items,
            key=lambda item: _order_content_key(item[5], item[2], item[3], item[4]),
        )
        placed_at = datetime(
            traded_on.year,
            traded_on.month,
            traded_on.day,
            tzinfo=as_of.tzinfo or UTC,
        )
        for seq, (_, _, quantity, price, side, _cols) in enumerate(ordered):
            out.append(
                AkdOrder(
                    order_id=f"{symbol}-{traded_on.isoformat()}-{seq}",
                    symbol=symbol,
                    side=side,
                    # AkdOrder.quantity is declared int - a fractional
                    # quantity would be truncated here, not rounded or
                    # preserved; this does not round-trip.
                    quantity=int(quantity),
                    price=price,
                    status="FILLED",  # order history is executed fills, not open orders
                    placed_at=placed_at,
                )
            )
    return out


def decode_exposure(text: str) -> dict | None:
    """Decode a GetExposureDynamic `<return>` body -> account money state.

    Plaintext: per-market rows `label,cols;v_REG;...|` then a summary line
    `~Cash;Pending;Exposure;OutstandingWorth;P/L;Loan;ShareWorth;Equity;FreeCash`
    followed by its values. We read that summary; FreeCash is the real buying
    power orders must be sized against.
    """
    body = maybe_decompress(text or "")
    sections = [s for s in body.split("|") if s.strip()]
    if len(sections) < 2:
        return None
    header, values = sections[-2], sections[-1]
    keys = [k.split(",")[0].strip().lstrip("~") for k in header.split(";")]
    vals = [v.strip() for v in values.split(";")]
    d = dict(zip(keys, vals, strict=False))

    def g(k: str) -> float | None:
        return _to_float(d.get(k, ""))

    out = {
        "cash_pkr": g("Cash"), "exposure_used_pkr": g("Exposure"),
        "share_worth_pkr": g("Share Worth"), "equity_pkr": g("Equity"),
        "free_cash_pkr": g("Free Cash"), "pl_pkr": g("Profit/Loss"),
    }
    # A body that yielded no recognisable money field is not a valid snapshot.
    return out if any(v is not None for v in out.values()) else None
