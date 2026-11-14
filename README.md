# AKD portfolio skill

A small, **read-only** tool that fetches your AKD TradeCast brokerage portfolio (holdings, cash / buying power, executed order history) as JSON, packaged as a Claude skill (`skills/akd-portfolio/`).

> **It reads from AKD; it cannot send orders.** Nothing in this repo sends an order to AKD.
> **Unofficial.** It speaks TradeCast's SOAP protocol as reverse-engineered from the client, not a supported public API. AKD can change it at any time. Read [docs/SECURITY.md](docs/SECURITY.md) before using it with a real account.

## Quick start

Requires **Python 3.11+** and `make`.

```bash
make install                                  # creates .venv and installs dependencies
export AKD_USER_ID=COAF00000                  # your AKD account
read -s AKD_PASSWORD && export AKD_PASSWORD   # your FULL AKD password, typed without echo
make portfolio                                # one login attempt, then JSON on stdout
```

Not using make? `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/pip install -e . --no-deps && .venv/bin/python -m akd portfolio`.

More accounts: add `AKD_USER_ID_2` / `AKD_PASSWORD_2`, `_3`, and so on. Output is `{"accounts": [...]}`, one entry per account; one that fails appears as `{"account", "error", "message"}` and does not stop the others. Exit code `0` only if every account worked, else the first failure's: `2` missing credentials / invalid config, `3` login rejected, `4` AKD unreachable. With no account configured the output is a single `{"error", "message"}`.

## Use it as a Claude skill

`skills/akd-portfolio/SKILL.md` tells Claude when to run the command and how to read the output. Export the two variables in your own shell before starting Claude; never paste the password into the chat.

## Output

```
{ "accounts": [ {
  "account", "fetched_at",
  "holdings": {"data": {"positions", "totals"}, "flags": [...]},
  "cash":     {"data": {"cash": {...}},        "flags": [...]},
  "orders":   {"data": {"orders": [...]},      "flags": [...]},
  "poll": {...}, "last_error": ... }, ... ] }
```

- **Honest "no data".** If a section could not be read, it is `null` with a `no_snapshot` flag, never `[]` or `0`: an empty list would claim "you own nothing".
- **Prices are AKD's, as of the snapshot.** "Market price" is the rate in AKD's own `MTM` column, **not a live PSX quote**. A number is `null` when AKD didn't send its inputs.
- **Order history is unverified.** The decoder works on real captures but has never been checked against the broker's own record of fills; the section carries `decoder_unverified`.
- **Two timestamps.** `as_of` is when the data last *changed*; `last_confirmed_at` is when AKD last *re-sent* it.
- **Account lockout.** Exactly one login attempt per run; a rejected login stops and is never retried. Don't script rapid reruns.
- **The password lives in memory only** and is never written, logged or printed.

## Documentation

- [docs/CONFIGURATION.md](docs/CONFIGURATION.md): environment variables
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): how the fetch works
- [docs/STRUCTURE.md](docs/STRUCTURE.md): file layout
- [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md): tests, adding a read operation
- [docs/SECURITY.md](docs/SECURITY.md), [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)
- [docs/PROTOCOL-MAP.md](docs/PROTOCOL-MAP.md), [docs/PROTOCOL-RESEARCH.md](docs/PROTOCOL-RESEARCH.md): the reverse-engineered protocol
# akd-portfolio-skill
