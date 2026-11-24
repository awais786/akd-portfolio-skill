---
name: akd-portfolio
description: Fetch the user's AKD (PSX) brokerage portfolio for every configured account — holdings, cash and order history — and present the stocks with profit (green) first and loss (red) second. Credentials come from the akd-app .env or the environment. Use when the user asks for their AKD or TradeCast portfolio, holdings, positions or P/L, asks for a review of what to do with them, or asks for a trade ticket (a buy or sell suggestion to place themselves).
---

# AKD portfolio

Read-only. Logs in to AKD once, reads holdings, cash and orders once, prints one JSON document. It never sends orders.

## Run it

Run the launcher next to this file. It works from any folder and finds the repo's `.env` itself:

```bash
"<this skill's directory>/run.sh"
```

(The first run creates a private `.venv` in the repo by itself, which takes a little while and needs Python 3.11+ and internet; setup messages go to stderr, so stdout stays one JSON document. If that fails, show the user the stderr message.)

**Only run it when the user asks for their portfolio, and run it once. If the portfolio data is no longer in this conversation (for example after the context was compacted), ask the user before running it again: never log in again silently.** Each run logs in once per configured account, and AKD locks accounts after repeated bad logins. Never re-run it to "double check", never loop it, and never open, print or `cat` the `.env` file.

Credentials come **only** from the environment (or the repo's git-ignored `.env`): `AKD_USER_ID` (the AKD account, e.g. `COAF00000`) and `AKD_PASSWORD` for the first account, then `AKD_USER_ID_2` / `AKD_PASSWORD_2`, `_3`, and so on for more accounts. Each account gets one login attempt per run.

- If they are not set, the command exits 2 with `missing_credentials`. Tell the user to put them in the repo's `.env` file or export them in their own shell. **Never ask the user to paste the password into the chat, never write it to any file yourself, never echo it or put it in a command line.**
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

Report what the data says. Do not invent figures for fields that are `null`.

## Review flags (only when the user asks for a review, guidance or "what to do")

Do not add these to a plain portfolio request. When asked, apply these rules **by hand to the JSON you already fetched** (no second run, no new login) and list only what triggers, most important first, one line each with the numbers:

- **Concentration:** one stock above 25% of its account's total market value; above 40% is strong. Weight = `market_value` / the account's `totals.total_market_value`.
- **Deep loser:** `pl_pct` below -15% (cut, hold or average down is the user's call; say which signals apply).
- **Big winner:** `pl_pct` above +20% (consider partial profit).
- **Overlap:** the same symbol in more than one account: show the combined value and its share of all accounts together.
- **Idle cash:** only if cash was asked for: free cash above 10% of an account.

Say "no flags" for an account with none. If the user wants current context on a flagged stock, you may look up its public PSX company page (`https://dps.psx.com.pk/company/<SYMBOL>`) for price, 52-week range and P/E; send only the ticker, never quantities, values or account IDs.

Never place an order through any tool. These thresholds are rules of thumb with no proven edge; they flag positions to think about, not instructions. Costs and capital gains tax are not included. End with: "Research, not licensed advice. Nothing was placed."

## Trade tickets (only when the user asks for specific trades)

(Dedicated skills exist for this: `/akd-sell-ticket` and `/akd-buy-ticket`. Use the details below if the user asks for tickets in plain words here.)

**You cannot place orders and must never try to.** There is no order code anywhere. A ticket is a suggestion the user places themselves in AKD's own app. Never ask for, accept or repeat a trading PIN.

When the user wants concrete trades after a review, draft each ticket as: symbol, BUY or SELL, whole-number quantity, and a **limit price** (limit orders only, at most 2 decimals), with a one-line reason. Check every ticket before showing it, using numbers from the portfolio JSON you already have (no new fetch, no new login):

```bash
# a SELL ticket: needs --held (shares of that stock in that account)
"<this skill's directory>/run.sh" ticket sell --symbol OGDC --qty 100 --price 315.00 \
    --held 500 --ref 315.87 --ref-source "AKD mark" --account COAF00000
# a BUY ticket: needs --free-cash (that account's free cash)
"<this skill's directory>/run.sh" ticket buy --symbol OGDC --qty 100 --price 315.00 \
    --free-cash 31025 --ref 315.87 --ref-source "AKD mark" --account COAF00000
```

The word after `ticket` is `buy` or `sell` (never a bare `buy` or `sell` command: that is blocked as order placement). `--held` is that account's quantity of the symbol (`holdings.data.positions[].quantity`, sell only), `--free-cash` is that account's free cash (`cash.data.cash.free_cash_pkr`, buy only), `--ref` is AKD's price for the stock (`market_price`; add `--ref-source "AKD mark"`). Use the figures for the same account as `--account`. The JSON answer has `allowed` and a `checks` list; exit 0 = allowed, 1 = a check failed, 2 = bad input. Show only tickets that are allowed; for a blocked one, say which check failed and why. A `warn` (no reference price) is allowed but say so. Add `--account <id>` so the approval prompt shows which account the ticket is for. If the user has installed the approval hook (`hooks/install_hook.py`), every ticket command stops for their approval and shows the order, and anything that looks like order placement is blocked. Never try to get around it. Always state the account the ticket is for, that costs and capital gains tax are not included, and "Nothing was placed: you place this yourself in AKD's app."

