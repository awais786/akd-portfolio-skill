#!/usr/bin/env python3
"""Claude Code PreToolUse hook for the AKD skill. Reads the tool call as JSON on stdin.

* A Bash command that runs `akd ticket` ALWAYS gets an approval prompt that shows the order (side, symbol,
  quantity, limit price, total, account). It never answers "allow", so no permission rule can wave it through.
* A ticket that breaks a limit is DENIED before it runs: quantity over AKD_TICKET_MAX_QTY (default 10,000),
  value over AKD_TICKET_MAX_VALUE (default PKR 100,000), price further than AKD_TICKET_PRICE_BAND_PCT (default
  10%) from --ref, or a zero/negative/odd quantity, price or reference. A broken limit value falls back to its
  default. A ticket this hook cannot read (unknown flag, abbreviation, missing value) is DENIED, not asked.
* A command that looks like order placement (`akd place|order|submit|execute|...`) is DENIED. No such
  command exists; this keeps it that way if someone ever adds one.
* Changing the limits file (`limits.env`) with Write/Edit or a Bash command asks first, so a limit cannot be
  loosened without you seeing it. (Needs the matcher from install_hook.py: Bash and the edit tools.)
* Everything else produces no output and is left to the normal permission flow.
* A payload it cannot read exits 2, which blocks the call (fail closed).

It only reads the command text. It never runs anything.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

ORDER_WORDS = {"place", "order", "orders", "submit", "execute", "buy", "sell", "send", "trade", "cancel", "amend", "modify"}
OPERATORS = {"&&", "||", "|", ";", "&", "(", ")"}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
SAFE_SUBCOMMANDS = {"", "portfolio", "ticket"}     # everything `python -m akd` does; any other word is asked about
EDIT_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
READ_ONLY = {"cat", "grep", "egrep", "fgrep", "head", "tail", "wc", "ls", "stat", "diff", "less", "more"}
TICKET_FLAGS = ("symbol", "qty", "price", "free-cash", "held", "ref", "ref-source", "account")  # as `akd ticket`
NOTE = "Nothing is placed: this only checks the ticket. You place the trade yourself in AKD's own app."
LIMITS_RE = re.compile(r"limits\.env(?!\.example)")
RUN_RE = re.compile(r"(run\.sh|akd-app|-m\s+akd|__main__\.py)\b[^|;&]*\bticket\b")

LIMIT_DEFAULTS = {"AKD_TICKET_MAX_VALUE": "100000", "AKD_TICKET_MAX_QTY": "10000", "AKD_TICKET_PRICE_BAND_PCT": "10"}
# <repo>/limits.env (this file is <repo>/skills/akd-portfolio/hooks/ticket_guard.py); AKD_LIMITS_FILE overrides it,
# and an empty AKD_LIMITS_FILE turns the file off.
LIMITS_FILE = Path(__file__).resolve().parents[3] / "limits.env"


# ---- reading the command ------------------------------------------------------------------------
def _segments(command: str) -> list[list[str]]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    tokens = list(lexer)
    segments, current = [], []
    for token in tokens:
        if token in OPERATORS:
            if current:
                segments.append(current)
            current = []
        else:
            current.append(token)
    if current:
        segments.append(current)
    return segments


def _expand(command: str, depth: int = 0) -> list[list[str]]:
    """All command segments, including those inside `bash -c "..."` and `eval "..."` (a few levels deep)."""
    out = []
    for segment in _segments(command):
        out.append(segment)
        if depth >= 3:
            continue
        for i, token in enumerate(segment):
            base = token.rsplit("/", 1)[-1]
            if base in SHELLS:
                for j in range(i + 1, len(segment) - 1):      # `bash -c "script" [name]`: the script follows -c
                    flag = segment[j]
                    if flag.startswith("-") and not flag.startswith("--") and "c" in flag[1:]:
                        out.extend(_expand(segment[j + 1], depth + 1))
                        break
            elif base == "eval" and i + 1 < len(segment):
                out.extend(_expand(" ".join(segment[i + 1:]), depth + 1))
    return out


def _akd_subcommand(segment: list[str]) -> tuple[str, list[str]] | None:
    """If this segment runs the AKD tool, return (subcommand, the arguments after it)."""
    for i, token in enumerate(segment):
        base = token.rsplit("/", 1)[-1]
        is_akd = (
            base == "run.sh"                               # the launcher, wherever it is (`./run.sh` after a cd too)
            or base == "akd-app"                           # the installed script
            or (base == "__main__.py" and "akd" in token)  # python src/akd/__main__.py
            or (token == "-m" and i + 1 < len(segment) and segment[i + 1] == "akd")  # python -m akd
        )
        if not is_akd:
            continue
        rest = segment[i + 2:] if token == "-m" else segment[i + 1:]
        if rest:
            return rest[0], rest[1:]
        return "", []
    return None


def _parse_ticket(args: list[str]) -> tuple[dict[str, str | None] | None, str | None]:
    """`ticket buy|sell --flag value ...` read EXACTLY the way the ticket command reads it (argparse, no
    abbreviations, `--qty=5` accepted). Returns (fields, None) or (None, why it cannot be read)."""
    if not args or args[0] not in ("buy", "sell"):
        return None, "say whether it is a buy or a sell: `ticket buy ...` or `ticket sell ...`"
    import argparse             # only on the ticket path: this hook runs before EVERY Bash call

    class _Parser(argparse.ArgumentParser):
        def error(self, message):  # never print or exit from inside a hook
            raise ValueError(message)

    parser = _Parser(add_help=False, exit_on_error=False, allow_abbrev=False)
    for flag in TICKET_FLAGS:
        parser.add_argument(f"--{flag}")
    try:
        ns = parser.parse_args(args[1:])
    except (ValueError, argparse.ArgumentError) as exc:
        return None, str(exc)
    return {**vars(ns), "side": args[0]}, None


# ---- the numbers --------------------------------------------------------------------------------
def _money(text: str | None) -> Decimal | None:
    try:
        value = Decimal(str(text))
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def _whole(text: str | None) -> int | None:
    value = _money(text)
    return int(value) if value is not None and value == value.to_integral_value() else None


def _describe(fields: dict[str, str | None]) -> str | None:
    qty, price = _whole(fields.get("qty")), _money(fields.get("price"))
    if qty is None or price is None or not fields.get("symbol"):
        return None
    total = (price * qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    where = f" | account: {fields['account']}" if fields.get("account") else ""
    return f"{str(fields['side']).upper()} {qty:,} {str(fields['symbol']).upper()} @ Rs {price} limit = PKR {total:,.2f}{where}"


def _limits_file_values() -> dict[str, str]:
    """Only the AKD_TICKET_* lines of the limits file. Nothing else in it is read; it is never `.env`."""
    path = os.environ.get("AKD_LIMITS_FILE", str(LIMITS_FILE))
    if not path:
        return {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return {}
    out = {}
    for line in text.splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key.strip() in LIMIT_DEFAULTS:
            out[key.strip()] = value.strip().strip("'\"")
    return out


def _limit(key: str, file_values: dict[str, str]) -> Decimal:
    """A limit: the environment, then the limits file, then the default. A bad value (not a number, infinite or
    not positive) falls back to the default: a typo in a limit must never mean "no limit"."""
    default = Decimal(LIMIT_DEFAULTS[key])
    value = _money(os.environ.get(key, file_values.get(key, LIMIT_DEFAULTS[key])))
    return value if value is not None and value > 0 else default


def _violations(fields: dict[str, str | None]) -> list[str]:
    """Why this ticket must NOT run at all (the hook denies it), independent of the ticket command's own check."""
    bad = []
    if not fields.get("symbol"):
        bad.append("--symbol is missing")
    qty, price = _whole(fields.get("qty")), _money(fields.get("price"))
    if qty is None or qty <= 0:
        bad.append(f"--qty {fields.get('qty')!r} must be a whole number above zero")
    if price is None or price <= 0:
        bad.append(f"--price {fields.get('price')!r} must be a number above zero")
    ref = None
    if fields.get("ref") is not None:
        ref = _money(fields["ref"])
        if ref is None or ref <= 0:
            bad.append(f"the reference price {fields['ref']!r} is not a positive number")
            ref = None
    if bad:
        return bad
    file_values = _limits_file_values()
    max_value, band, max_qty = (_limit("AKD_TICKET_MAX_VALUE", file_values), _limit("AKD_TICKET_PRICE_BAND_PCT", file_values),
                                _limit("AKD_TICKET_MAX_QTY", file_values))
    if qty > max_qty:
        bad.append(f"quantity {qty:,} is over the limit of {max_qty:,.0f} shares (AKD_TICKET_MAX_QTY)")
    value = price * qty
    if value > max_value:
        bad.append(f"value PKR {value:,.2f} is over the limit of PKR {max_value:,.0f} per order (AKD_TICKET_MAX_VALUE)")
    if ref is not None:
        away = abs(price - ref) / ref * 100
        if away > band:
            bad.append(f"price Rs {price} is {away:.1f}% from the reference Rs {ref}, over the {band:g}% limit "
                       "(AKD_TICKET_PRICE_BAND_PCT): looks like a typo")
    return bad


# ---- the limits file itself ---------------------------------------------------------------------
def _is_limits_path(path: str) -> bool:
    if Path(path).name == "limits.env":
        return True
    configured = os.environ.get("AKD_LIMITS_FILE") or str(LIMITS_FILE)
    try:
        return Path(path).resolve() == Path(configured).resolve()
    except OSError:
        return False


def _touches_limits(command: str, segments: list[list[str]] | None) -> bool:
    """Does this Bash command write the limits file? Plain reads (cat, grep, ls...) do not count."""
    if not LIMITS_RE.search(command):
        return False
    if segments is None:
        return True                                     # we cannot read it, so we cannot call it a read
    for segment in segments:
        if any(LIMITS_RE.search(t) for t in segment) or any(t.startswith(">") for t in segment):
            if segment[0].rsplit("/", 1)[-1] not in READ_ONLY or any(t.startswith(">") for t in segment):
                return True
    return False


LIMITS_MESSAGE = ("This changes your order limits (limits.env), which the ticket guard enforces. Approve only if you "
                  "meant to change a limit.")


# ---- answering ----------------------------------------------------------------------------------
def _answer(decision: str, reason: str, message: str | None = None) -> None:
    out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                  "permissionDecisionReason": reason}}
    if message:
        out["systemMessage"] = message
    json.dump(out, sys.stdout)


def _looks_like_akd_ticket(command: str) -> bool:
    return "akd" in command and any(word in command for word in ("ticket", *ORDER_WORDS))


def main() -> int:
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"ticket_guard: unreadable hook payload ({type(exc).__name__}); blocking to be safe", file=sys.stderr)
        return 2
    if not isinstance(payload, dict):
        print("ticket_guard: the hook payload is not a JSON object; blocking to be safe", file=sys.stderr)
        return 2
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    if tool in EDIT_TOOLS:
        path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        if path and _is_limits_path(path):
            _answer("ask", f"{LIMITS_MESSAGE}\n{path}", LIMITS_MESSAGE)
        return 0
    if tool != "Bash":
        return 0
    command = str(tool_input.get("command", ""))
    try:
        return _decide(command)
    except Exception as exc:  # noqa: BLE001 - a bug here must never block every Bash call in every session
        print(f"ticket_guard: internal error ({type(exc).__name__}); not blocking", file=sys.stderr)
        if _looks_like_akd_ticket(command) or LIMITS_RE.search(command):
            _answer("ask", f"The ticket guard hit an internal error, so check it yourself. {NOTE}\n{command}")
        return 0


def _hard_to_read(segments: list[list[str]]) -> bool:
    """Shapes of command we cannot follow: a script piped into a shell, xargs, source."""
    for segment in segments:
        base = segment[0].rsplit("/", 1)[-1]
        if base in SHELLS or base in ("xargs", "source", "."):
            return True
    return False


def _decide(command: str) -> int:
    try:
        segments = _expand(command)
    except ValueError:  # unbalanced quotes: we cannot read it, so we cannot call it safe
        segments = None
    touches_limits = _touches_limits(command, segments)
    if segments is None:
        if RUN_RE.search(command) or touches_limits:
            _answer("ask", f"Could not read this command, so check it yourself. {NOTE}\n{command}")
        return 0

    tickets, denied, unknown = [], None, None
    for segment in segments:
        found = _akd_subcommand(segment)
        if not found:
            continue
        sub, args = found
        if sub in ORDER_WORDS:
            denied = sub
        elif sub == "ticket":
            tickets.append(_parse_ticket(args))
        elif sub not in SAFE_SUBCOMMANDS and any("akd" in t for t in segment):   # not some other project's run.sh
            unknown = sub
    if denied:
        _answer("deny", f"Blocked: '{denied}' looks like order placement. No order placement is allowed here: "
                        "this project can only check tickets, and you place trades yourself in AKD's own app.")
        return 0

    refused = []
    for n, (fields, why) in enumerate(tickets, 1):
        problems = [f"cannot read this ticket: {why}"] if fields is None else _violations(fields)
        if problems:
            shown = (_describe(fields) if fields else None) or "(unreadable ticket)"
            refused.append(f"{n}. {shown}\n   - " + "\n   - ".join(problems))
    if refused:
        _answer("deny", "Blocked by the ticket limits (nothing was run):\n" + "\n".join(refused) +
                "\nFix the ticket, or change the limit on purpose in limits.env. " + NOTE)
        return 0

    unknown_note = (f"This runs an AKD command the guard does not know ({unknown!r}); the tool only has "
                    "`portfolio` and `ticket`. Check it yourself.") if unknown is not None else ""
    if not tickets:
        if unknown_note:
            _answer("ask", f"{unknown_note}\n{command}", unknown_note)
        elif RUN_RE.search(command) and _hard_to_read(segments):
            _answer("ask", f"Could not read this AKD ticket command, so check it yourself. {NOTE}\n{command}")
        elif touches_limits:
            _answer("ask", f"{LIMITS_MESSAGE}\n{command}", LIMITS_MESSAGE)
        return 0

    lines = []
    for n, (fields, _why) in enumerate(tickets, 1):
        text = _describe(fields)
        note = "" if fields.get("ref") is not None else "  [no reference price given: the price could not be checked]"
        lines.append(f"{n}. {text}{note}")
    head = "AKD ticket check: approve to run it." if len(tickets) == 1 else f"AKD ticket check ({len(tickets)} tickets): approve to run them."
    if touches_limits:
        head += f"\n{LIMITS_MESSAGE}"
    if unknown_note:
        head += f"\n{unknown_note}"
    body = "\n".join(lines)
    _answer("ask", f"{head}\n{body}\n{NOTE}", f"{head}\n{body}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
