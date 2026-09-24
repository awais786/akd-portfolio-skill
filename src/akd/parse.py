"""Import an AKD TradeCast portfolio export into the platform.

TradeCast can export Portfolio Watch / Outstanding positions / Account
Statement as CSV. The exact column layout varies by broker config, so this
parser is deliberately tolerant: it sniffs the delimiter, finds the header row,
and maps columns by keyword rather than fixed position. What it will not do is
guess a quantity or a cost - a row missing either is reported, never invented.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(slots=True)
class ImportedFill:
    """One executed order from an AKD order-history export - a dated trade."""
    symbol: str
    side: str            # "BUY" | "SELL"
    quantity: float
    price: float
    value: float | None
    traded_on: date


_OH_DATE_FORMATS = ("%b %d, %Y", "%b %d %Y", "%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y")


def _oh_date(s: str) -> date | None:
    s = (s or "").strip()
    for fmt in _OH_DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_orderhistory(text: str) -> list[ImportedFill]:
    """Parse AKD GetOrderHistory rows into dated fills.

    Wire format (pipe-joined records, semicolon fields):
        SYMBOL;QTY;PRICE;VALUE;SIDE;SETTLEMENT;TRADE_DATE;SETTLE_DATE;
    e.g. ``MUGHAL;532;86.65;46179.99;BUY;T+1REG;Jun 15, 2026;Jun 16, 2026;``
    Also tolerates a newline/CSV-delimited paste of the same columns. Only rows
    with a symbol, a quantity, a side and a parseable trade date survive.
    """
    fills: list[ImportedFill] = []
    # records may be | or newline separated
    raw_records = re.split(r"\||\r?\n", text)
    for rec in raw_records:
        rec = rec.strip()
        if not rec:
            continue
        c = [x.strip() for x in rec.split(";")]
        if len(c) < 7:
            continue
        symbol = c[0].upper()
        if not _SYMBOL_RE.match(symbol):
            continue
        side = c[4].upper()
        if side not in ("BUY", "SELL"):
            continue
        qty = _to_number(c[1])
        price = _to_number(c[2])
        traded = _oh_date(c[6])
        if not qty or qty <= 0 or price is None or traded is None:
            continue
        fills.append(ImportedFill(symbol=symbol, side=side, quantity=qty, price=price,
                                  value=_to_number(c[3]), traded_on=traded))
    return fills


@dataclass(slots=True)
class ImportedPosition:
    symbol: str
    quantity: float
    avg_cost: float | None
    market_price: float | None = None
    raw: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class ImportResult:
    positions: list[ImportedPosition]
    skipped: list[dict[str, str]]
    detected_columns: dict[str, str]
    delimiter: str
    row_count: int
    warnings: list[str] = field(default_factory=list)


# Header keywords, most specific first. A column matches the first bucket whose
# any keyword is a substring of the (normalised) header.
_COLUMN_KEYWORDS = {
    "symbol": ["symbol", "scrip", "ticker", "security", "company"],
    "quantity": ["net qty", "holding", "quantity", "qty", "volume", "shares", "balance", "position"],
    "avg_cost": ["avg cost", "average cost", "avg rate", "avg. rate", "cost rate", "avg price",
                 "average price", "cost price", "purchase rate", "buy rate", "cost"],
    "market_price": ["market rate", "market price", "last price", "ltp", "current price", "close"],
}

_NUM = re.compile(r"-?\(?\s*[\d,]*\.?\d+\s*\)?")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _to_number(text: str | None) -> float | None:
    if text is None:
        return None
    s = str(text).strip()
    if not s or s in {"-", "--", "N/A", "n/a"}:
        return None
    negative = s.startswith("(") and s.endswith(")")
    s = s.replace(",", "").replace("(", "").replace(")", "").strip()
    m = re.search(r"-?\d*\.?\d+", s)
    if not m:
        return None
    value = float(m.group(0))
    return -value if negative else value


def _sniff_delimiter(text: str) -> str:
    sample = "\n".join(text.splitlines()[:20])
    counts = {d: sample.count(d) for d in [",", "\t", ";", "|"]}
    best = max(counts, key=counts.get)
    return best if counts[best] > 0 else ","


def _find_header(rows: list[list[str]]) -> int:
    """The header is the first row whose cells look like column names - i.e.
    contain a recognisable symbol/quantity keyword and are mostly non-numeric."""
    for i, row in enumerate(rows[:15]):
        joined = _norm(" ".join(row))
        has_symbol = any(k in joined for k in _COLUMN_KEYWORDS["symbol"])
        has_qty = any(k in joined for k in _COLUMN_KEYWORDS["quantity"])
        if has_symbol and has_qty:
            return i
    return 0


def _map_columns(header: list[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for idx, cell in enumerate(header):
        norm = _norm(cell)
        for field_name, keywords in _COLUMN_KEYWORDS.items():
            if field_name in mapping:
                continue
            if any(kw in norm for kw in keywords):
                mapping[field_name] = idx
                break
    return mapping


_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9]{1,11}$")


def parse(text: str) -> ImportResult:
    warnings: list[str] = []
    delimiter = _sniff_delimiter(text)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = [r for r in reader if any(cell.strip() for cell in r)]
    if not rows:
        return ImportResult([], [], {}, delimiter, 0, ["file is empty"])

    header_idx = _find_header(rows)
    header = rows[header_idx]
    mapping = _map_columns(header)

    if "symbol" not in mapping or "quantity" not in mapping:
        return ImportResult(
            [], [], {}, delimiter, len(rows),
            [
                "could not find a Symbol and a Quantity column. Header seen: "
                + " | ".join(header),
                ("Export Portfolio Watch or Outstanding positions from TradeCast as CSV, "
                "or paste the columns with a header row."),
            ],
        )
    if "avg_cost" not in mapping:
        warnings.append(
            "no average-cost column detected - positions will import with quantity only, "
            "and unrealised P&L cannot be computed until you set a cost"
        )

    positions: list[ImportedPosition] = []
    skipped: list[dict[str, str]] = []
    for row in rows[header_idx + 1 :]:
        if len(row) <= mapping["symbol"]:
            continue
        raw = {header[i] if i < len(header) else f"col{i}": v for i, v in enumerate(row)}
        symbol = (row[mapping["symbol"]] or "").strip().upper()
        symbol = re.split(r"[\s;|/]", symbol)[0] if symbol else symbol
        qty = _to_number(row[mapping["quantity"]]) if len(row) > mapping["quantity"] else None

        if not symbol or not _SYMBOL_RE.match(symbol):
            continue  # header remnant, total row, or blank
        if qty is None or qty == 0:
            skipped.append({"symbol": symbol, "reason": "zero or unreadable quantity", **raw})
            continue

        cost = _to_number(row[mapping["avg_cost"]]) if "avg_cost" in mapping and len(row) > mapping["avg_cost"] else None
        mkt = _to_number(row[mapping["market_price"]]) if "market_price" in mapping and len(row) > mapping["market_price"] else None
        positions.append(ImportedPosition(symbol=symbol, quantity=qty, avg_cost=cost, market_price=mkt, raw=raw))

    detected = {k: header[v] if v < len(header) else f"col{v}" for k, v in mapping.items()}
    if not positions:
        warnings.append("no valid positions found - check that rows have a symbol and a non-zero quantity")
    return ImportResult(positions, skipped, detected, delimiter, len(rows), warnings)
