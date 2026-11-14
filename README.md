# AKD portfolio skill

A **read-only Claude skill** that fetches your AKD (PSX) brokerage portfolio, for one or more accounts, so Claude can show it and analyse it. Ask "show my AKD portfolio" and get your stocks with the ones in profit (🟢) first and the ones in loss (🔴) second.

> **It reads from AKD; it cannot send orders.**
> **Unofficial.** It speaks TradeCast's SOAP protocol as reverse-engineered from the client, not a supported public API. AKD can change it at any time. Read [docs/SECURITY.md](docs/SECURITY.md) before using it with a real account.

## Install

```bash
git clone https://github.com/awais786/akd-portfolio-skill.git
ln -s "$PWD/akd-portfolio-skill/skills/akd-portfolio" ~/.claude/skills/akd-portfolio
```

That's it: no environment to set up. The first time the skill runs it creates its own private `.venv` inside the repo (needs Python 3.11+ and an internet connection, once).

## Add your accounts

Create a `.env` file in the repo folder (it is git-ignored) and keep it private:

```bash
cd akd-portfolio-skill
cat > .env <<'EOF'
AKD_USER_ID=COAF00000
AKD_PASSWORD=your-full-akd-password
EOF
chmod 600 .env
```

More accounts: add `AKD_USER_ID_2` / `AKD_PASSWORD_2`, then `_3`, and so on. The password sits in plain text in that file, so keep it on a machine you trust. Claude never needs to see it: the skill reads it itself and never prints it.

## Use it

In Claude Code, type `/akd-portfolio` or just ask for your portfolio. Each run makes **one login attempt per account**. A wrong password costs an attempt and AKD can lock accounts after repeated failures, so check the password before running, and don't re-run in a loop.

You can also run it directly: `skills/akd-portfolio/run.sh` prints the JSON below.

## What you get

For each account: holdings (quantity, average cost, AKD's price, value, P/L and P/L %), cash and order history, as JSON:

```
{ "accounts": [ { "account", "fetched_at",
    "holdings": {"data": {"positions", "totals"}, "flags": [...]},
    "cash":     {"data": {"cash": {...}},        "flags": [...]},
    "orders":   {"data": {"orders": [...]},      "flags": [...]},
    "poll": {...}, "last_error": ... }, ... ] }
```

An account that fails (for example a rejected login) appears as `{"account", "error", "message"}` and does not stop the others. Exit code `0` means every account worked; otherwise it is the first failure's: `2` missing credentials or bad config, `3` login rejected, `4` AKD unreachable.

- **Honest "no data".** A section that could not be read is `null` with a `no_snapshot` flag, never `[]` or `0`: an empty list would claim "you own nothing".
- **Prices are AKD's, as of the snapshot**, not live PSX quotes.
- **Order history is unverified**, and may not load at all yet; the section carries `decoder_unverified`.
- **The password lives in memory only** and is never written to disk by the tool, logged or printed.

## Documentation

- [docs/SECURITY.md](docs/SECURITY.md), [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)
- [docs/CONFIGURATION.md](docs/CONFIGURATION.md): all environment variables
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/STRUCTURE.md](docs/STRUCTURE.md), [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md): for contributors (tests, `make` targets)
- [docs/PROTOCOL-MAP.md](docs/PROTOCOL-MAP.md), [docs/PROTOCOL-RESEARCH.md](docs/PROTOCOL-RESEARCH.md): the reverse-engineered protocol
