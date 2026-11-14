---
name: akd-portfolio
description: Fetch the user's AKD (PSX) brokerage portfolio for every configured account — holdings, cash and order history — and present the stocks with profit (green) first and loss (red) second. Credentials come from the akd-app .env or the environment. Use when the user asks for their AKD or TradeCast portfolio, holdings, positions or P/L.
---

# AKD portfolio

Read-only. Logs in to AKD once, reads holdings, cash and orders once, prints one JSON document. It never sends orders.

## Run it

Run the launcher next to this file. It works from any folder and finds the repo's `.env` itself:

```bash
"<this skill's directory>/run.sh"
```

(If the skill is installed as a symlink in `~/.claude/skills/`, that path is fine. If `.venv` is missing it says so: run `make install` in the `akd-app` repo.)

**Only run it when the user asks for their portfolio, and run it once.** Each run logs in once per configured account, and AKD locks accounts after repeated bad logins. Never re-run it to "double check", never loop it, and never open, print or `cat` the `.env` file.

Credentials come **only** from the environment (or the repo's git-ignored `.env`): `AKD_USER_ID` (the AKD account, e.g. `COAF00000`) and `AKD_PASSWORD` for the first account, then `AKD_USER_ID_2` / `AKD_PASSWORD_2`, `_3`, and so on for more accounts. Each account gets one login attempt per run.

- If they are not set, the command exits 2 with `missing_credentials`. Tell the user to export them in their own shell. **Never ask the user to paste the password into the chat, never write it to a file, never echo it or put it in a command line.**
- Exit code is 0 only if every account worked; otherwise it is the first failure's code, and the JSON still holds the accounts that did work. A failed account appears as `{"account", "error", "message"}` in its place: tell the user which one failed.
- A rejected login exits 3 (`login_rejected`). **Stop and tell the user. Do not retry**: AKD locks accounts after repeated bad logins. One attempt per run, by design.
- AKD unreachable exits 4 (`akd_unreachable`); it may be outside PSX trading hours. A retry later is fine.

## Reading the JSON

```
{ "accounts": [ one entry per account, in order; each is either the object below or
                 {"account", "error", "message"} when that account failed ] }

each account entry:
{ "account", "fetched_at",
  "holdings": {"data": {"positions": [...], "totals": {...}}, "flags": [...]},
  "cash":     {"data": {"cash": {...}}, "flags": [...]},
  "orders":   {"data": {"orders": [...]},  "flags": [...]},
  "poll": {...}, "last_error": ... }
```

- **`null` plus a `no_snapshot` flag means unknown, not empty.** Never report "you own nothing" unless `positions` is a real empty list. Check `poll` and `last_error` for why a section is missing.
- **Prices are AKD's own mark at snapshot time, not live PSX quotes.** P/L is derived from that mark and your average cost.
- **Orders carry `decoder_unverified`**: the order-history decoder has never been checked against AKD's own record. Say so when you cite fills.
- `as_of` is when the data last changed; `last_confirmed_at` is when AKD last re-sent it.

## Presenting the holdings: profit table first, loss table second

Start each account with a header line `## Account <account>` (the `account` field of the JSON, e.g. `## Account COAF00000`). When there is more than one account, repeat the whole block below once per account, each under its own header, and never merge accounts into one table.

Then show that account's positions as **two separate tables, never mixed**, with **no section headings, no counts and no "none" lines**:

1. First the table of stocks in profit (`pl_total > 0`), every row prefixed 🟢, sorted by `pl_total` descending.
2. Then the table of stocks in loss (`pl_total < 0`), every row prefixed 🔴, sorted by `pl_total` ascending (biggest loss first).

If one group has no stocks, show only the other table: write nothing for the empty group, not even a heading or "none". Columns for both: Symbol, Qty, Avg cost, AKD price, Value (`market_value`), P/L (`pl_total`), P/L % (`pl_pct`). No per-group subtotals. A position with `pl_total == 0` goes in a short "➖ Flat" line, and one whose `pl_total` is `null` goes in a short "❔ P/L unknown" line (never counted as profit or loss); omit these lines when empty. End with one line of overall `totals` (cost, value, P/L). **By default show only the stocks:** do not show cash or orders, and do not mention a failed or missing orders section, unless the user asks for them. A terminal cannot colour text, so the 🟢/🔴 markers are the colours.

Report what the data says. Do not invent figures for fields that are `null`. Analysis guidance (allocation, concentration, risk) is not part of this skill yet.
