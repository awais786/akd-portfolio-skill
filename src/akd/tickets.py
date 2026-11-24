"""Trade tickets: validate a proposed order and run pre-trade checks. NOTHING IS EVER SENT.

A ticket is a suggestion the person places themselves in AKD's own app. This module only answers
"is this ticket sensible for this account right now?". There is no gateway, no PIN, no network, and no
state (so no daily-order or duplicate checks). Limit orders only. Unknown cash or holdings FAIL closed.
Limits are variables: AKD_TICKET_MAX_VALUE, AKD_TICKET_MAX_QTY, AKD_TICKET_PRICE_BAND_PCT and
AKD_TICKET_FEE_BUFFER_PCT. Order of precedence: the environment, then `limits.env` in the repo (or the file named
by AKD_LIMITS_FILE; empty disables it), then the defaults in DEFAULTS.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

__all__ = ["DEFAULTS", "Check", "Facts", "Limits", "Ticket", "allowed", "check_ticket", "parse_ticket",
           "read_limits_file", "run_ticket"]

# The one place the default limits live in this module (limits.env.example and the approval hook mirror
# them; tests keep the three in step).
DEFAULTS = {"AKD_TICKET_MAX_VALUE": "100000", "AKD_TICKET_MAX_QTY": "10000",
            "AKD_TICKET_PRICE_BAND_PCT": "10", "AKD_TICKET_FEE_BUFFER_PCT": "1"}
DEFAULT_LIMITS_FILE = Path(__file__).resolve().parents[2] / "limits.env"


def read_limits_file(path: str | Path) -> dict[str, str]:
    """The AKD_TICKET_* lines of a limits file (KEY=VALUE, `#` comments). Every other line is ignored on
    purpose: this file is for limits only, never for secrets, and nothing else in it is ever read."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return {}
    out = {}
    for line in text.splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key.strip() in DEFAULTS:
            out[key.strip()] = value.strip().strip("'\"")
    return out

SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9]{1,11}$")
SIDES = ("BUY", "SELL")
CENT = Decimal("0.01")


@dataclass(frozen=True)
class Ticket:
    symbol: str
    side: str             # BUY | SELL
    quantity: int
    limit_price: Decimal  # PKR per share, at most 2 decimals. Limit orders only: no market orders.

    @property
    def value(self) -> Decimal:
        return (self.limit_price * self.quantity).quantize(CENT, rounding=ROUND_HALF_UP)


def parse_ticket(symbol, side, quantity, limit_price) -> Ticket:
    """Validate raw input into a Ticket. Raises ValueError with a message meant for the person."""
    sym = str(symbol or "").strip().upper()
    if not SYMBOL_RE.match(sym):
        raise ValueError("symbol: 2-12 letters/digits, starting with a letter")
    sd = str(side or "").strip().upper()
    if sd not in SIDES:
        raise ValueError("side must be BUY or SELL")
    if isinstance(quantity, bool) or not isinstance(quantity, int | float | str):
        raise ValueError("quantity must be a whole number")
    try:
        q = Decimal(str(quantity))
    except InvalidOperation as exc:
        raise ValueError("quantity must be a whole number") from exc
    if not q.is_finite():                     # NaN, sNaN and Infinity would crash the comparisons below
        raise ValueError("quantity must be a whole number")
    if q != q.to_integral_value() or q <= 0:
        raise ValueError("quantity must be a whole number greater than 0")
    if isinstance(limit_price, bool):
        raise ValueError("limit price must be a number")
    try:
        price = Decimal(str(limit_price))
    except InvalidOperation as exc:
        raise ValueError("limit price must be a number") from exc
    if not price.is_finite() or price <= 0:
        raise ValueError("limit price must be greater than 0")
    try:
        two_places = price == price.quantize(CENT)
    except InvalidOperation:                  # too many digits to round to cents
        two_places = False
    if not two_places:
        raise ValueError("limit price can have at most 2 decimal places")
    return Ticket(sym, sd, int(q), price)


@dataclass(frozen=True)
class Limits:
    max_value: float       # PKR per order
    price_band_pct: float  # typo guard against the reference price
    fee_buffer_pct: float  # cash headroom for fees/taxes on a BUY
    max_qty: float = float(DEFAULTS["AKD_TICKET_MAX_QTY"])  # shares per order


@dataclass(frozen=True)
class Facts:
    """What the checks need, taken from the person's own fetched data. None = unknown."""
    free_cash: float | None
    held_qty: int | None
    ref_price: float | None
    ref_source: str | None


@dataclass(frozen=True)
class Check:
    name: str
    status: str    # pass | fail | warn
    message: str


def check_ticket(ticket: Ticket, facts: Facts, lim: Limits) -> list[Check]:
    """Every check, in a fixed order. A check that cannot be evaluated for lack of data FAILS closed for
    the ones that protect money (cash, holdings) and only warns for the advisory one (price reference)."""
    out: list[Check] = []

    def add(name, ok, good, bad):
        out.append(Check(name, "pass" if ok else "fail", good if ok else bad))

    value = float(ticket.value)
    add("Order size", value <= lim.max_value,
        f"PKR {value:,.2f} is within the per-order limit of PKR {lim.max_value:,.0f}.",
        f"PKR {value:,.2f} is over the per-order limit of PKR {lim.max_value:,.0f} (AKD_TICKET_MAX_VALUE).")

    add("Order quantity", ticket.quantity <= lim.max_qty,
        f"{ticket.quantity:,} shares is within the per-order limit of {lim.max_qty:,.0f}.",
        f"{ticket.quantity:,} shares is over the per-order limit of {lim.max_qty:,.0f} (AKD_TICKET_MAX_QTY).")

    if facts.ref_price is None:
        out.append(Check("Price sanity", "warn", "No reference price available, so the price could not be sanity-checked."))
    else:
        dev = (float(ticket.limit_price) - facts.ref_price) / facts.ref_price * 100
        add("Price sanity", abs(dev) <= lim.price_band_pct,
            f"Limit {ticket.limit_price} is {dev:+.1f}% from the reference {facts.ref_price:g} ({facts.ref_source}).",
            f"Limit {ticket.limit_price} is {dev:+.1f}% from the reference {facts.ref_price:g} ({facts.ref_source}); "
            f"more than {lim.price_band_pct:g}% away looks like a typo (AKD_TICKET_PRICE_BAND_PCT).")

    if ticket.side == "BUY":
        need = value * (1 + lim.fee_buffer_pct / 100)
        if facts.free_cash is None:
            out.append(Check("Enough cash", "fail", "Free cash is unknown."))
        else:
            add("Enough cash", facts.free_cash >= need,
                f"Needs about PKR {need:,.2f} (incl. a {lim.fee_buffer_pct:g}% allowance for fees/taxes); PKR {facts.free_cash:,.2f} is free.",
                f"Needs about PKR {need:,.2f} (incl. a {lim.fee_buffer_pct:g}% allowance for fees/taxes) but only PKR {facts.free_cash:,.2f} is free.")
    else:
        if facts.held_qty is None:
            out.append(Check("Shares held", "fail", "Holdings are unknown."))
        else:
            add("Shares held", ticket.quantity <= facts.held_qty,
                f"{facts.held_qty:,} shares of {ticket.symbol} held; selling {ticket.quantity:,}.",
                f"{facts.held_qty:,} shares of {ticket.symbol} held, so {ticket.quantity:,} can't be sold (no short selling).")
    return out


def allowed(checks: list[Check]) -> bool:
    return all(c.status != "fail" for c in checks)


def _limits(env: Mapping[str, str]) -> Limits:
    path = env.get("AKD_LIMITS_FILE", str(DEFAULT_LIMITS_FILE))
    merged = {**DEFAULTS, **(read_limits_file(path) if path else {}),
              **{k: v for k, v in env.items() if k in DEFAULTS}}

    def num(key: str, *, zero_ok: bool = False) -> float:
        """A bad value (not a number, infinite, or not positive) falls back to the default: a typo in a
        limit must never mean "no limit"."""
        default = float(DEFAULTS[key])
        try:
            value = float(merged[key])
        except ValueError:
            return default
        return value if math.isfinite(value) and (value > 0 or (zero_ok and value == 0)) else default
    return Limits(num("AKD_TICKET_MAX_VALUE"), num("AKD_TICKET_PRICE_BAND_PCT"),
                  num("AKD_TICKET_FEE_BUFFER_PCT", zero_ok=True), num("AKD_TICKET_MAX_QTY"))


USAGE = (
    "use `ticket buy --symbol X --qty N --price P --free-cash C` or "
    "`ticket sell --symbol X --qty N --price P --held H` "
    "(optional: --account ID, --ref R, --ref-source TEXT)"
)


def run_ticket(argv: list[str], env: Mapping[str, str], out) -> int:
    """`python -m akd ticket buy|sell ...`: one JSON document on `out`. Exit 0 allowed, 1 blocked by a
    check, 2 bad input. The document always says `"placed": false`: this command can never place an order.
    `buy` needs `--free-cash` (the account's free cash); `sell` needs `--held` (shares of that stock held)."""
    def fail(message: str) -> int:
        json.dump({"error": message, "placed": False}, out, indent=2)
        out.write("\n")
        return 2

    if not argv or argv[0] not in ("buy", "sell"):
        return fail(f"say whether it is a buy or a sell: {USAGE}")
    side, rest = argv[0].upper(), argv[1:]
    # allow_abbrev=False: `--qt 5` must not silently mean `--qty 5` (the approval hook reads flags exactly)
    parser = argparse.ArgumentParser(prog=f"akd ticket {argv[0]}", add_help=False, exit_on_error=False,
                                     allow_abbrev=False)
    for flag in ("symbol", "qty", "price", "free-cash", "held", "ref", "ref-source", "account"):
        parser.add_argument(f"--{flag}")
    try:
        args = parser.parse_args(rest)
        needed = "free_cash" if side == "BUY" else "held"
        if getattr(args, needed) is None:
            flag = "--" + needed.replace("_", "-")
            what = "the account's free cash" if side == "BUY" else "the shares of that stock held in the account"
            return fail(f"{argv[0]} needs {flag} ({what}). {USAGE}")
        ticket = parse_ticket(args.symbol, side, args.qty, args.price)
        facts = Facts(
            free_cash=_optional_float(args.free_cash, "--free-cash"),
            held_qty=_optional_int(args.held, "--held"),
            ref_price=_optional_float(args.ref, "--ref"),
            ref_source=args.ref_source,
        )
        if facts.ref_price is not None and facts.ref_price <= 0:
            raise ValueError("--ref must be a number above zero")
        if facts.free_cash is not None and facts.free_cash < 0:
            raise ValueError("--free-cash can't be negative")
        if facts.held_qty is not None and facts.held_qty < 0:
            raise ValueError("--held can't be negative")
    except (ValueError, argparse.ArgumentError, SystemExit) as exc:
        return fail(str(exc) or f"invalid arguments. {USAGE}")
    try:
        checks = check_ticket(ticket, facts, _limits(env))
    except ArithmeticError as exc:            # belt and braces: never a traceback, never exit 1 for a crash
        return fail(f"could not evaluate this ticket ({type(exc).__name__}); check the numbers. {USAGE}")
    ok = allowed(checks)
    json.dump({
        "account": args.account,  # a label only: shown to the person, never used by the checks
        "ticket": {"symbol": ticket.symbol, "side": ticket.side, "quantity": ticket.quantity,
                   "limit_price": str(ticket.limit_price), "value": str(ticket.value)},
        "allowed": ok,
        "checks": [{"name": c.name, "status": c.status, "message": c.message} for c in checks],
        "placed": False,
    }, out, indent=2)
    out.write("\n")
    return 0 if ok else 1


def _optional_float(value: str | None, flag: str) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except ValueError:
        raise ValueError(f"{flag} must be a number") from None
    if not math.isfinite(number):
        raise ValueError(f"{flag} must be a finite number")
    return number


def _optional_int(value: str | None, flag: str) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"{flag} must be a whole number") from None
