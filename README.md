# AKD portfolio skill

A **read-only Claude skill** that fetches your AKD (PSX) brokerage portfolio, for one or more accounts, so Claude can show it and analyse it. Ask "show my AKD portfolio" and get your stocks with the ones in profit (🟢) first and the ones in loss (🔴) second.

> **It reads from AKD; it cannot send orders.**
> **Unofficial.** It speaks TradeCast's SOAP protocol as reverse-engineered from the client, not a supported public API. AKD can change it at any time. Read [docs/SECURITY.md](docs/SECURITY.md) before using it with a real account.

## Install

```bash
git clone https://github.com/awais786/akd-portfolio-skill.git
for s in akd-portfolio akd-sell-ticket akd-buy-ticket; do
  ln -s "$PWD/akd-portfolio-skill/skills/$s" ~/.claude/skills/$s
done
```

That links three skills: `/akd-portfolio` (the fetch and review) and `/akd-sell-ticket` and `/akd-buy-ticket` (trade tickets). The two ticket skills use the fetch skill's launcher, so link all three.

That's it: no environment to set up. The first time the skill runs it creates its own private `.venv` inside the repo (needs Python 3.11+ and an internet connection, once). It installs exact, hash-checked versions from `requirements.lock`, so nothing unpinned is downloaded.

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

## All commands

**In a Claude Code session** (these are things you say; Claude runs the commands for you):

| Say | What happens |
|---|---|
| `/akd-portfolio` or "show my AKD portfolio" | Fetches every account (one login each) and shows stocks in profit (🟢) first, then loss (🔴) |
| "review my portfolio, what should I do?" | The same, plus review flags: oversized positions, deep losers, big winners, overlap across accounts |
| `/akd-sell-ticket 500 MLCF 105 COAF00000` (quantity, stock, limit price, account) | Checks a **sell** ticket against that account and writes it up. Asks for anything missing. Nothing is placed |
| `/akd-buy-ticket 100 OGDC 315 COAF00000` | Checks a **buy** ticket the same way, and warns if it would make the stock too big a share of the account |
| "make a sell ticket for 500 MLCF in COAF00000 at 105" | The same as `/akd-sell-ticket`, in plain words |

**In your terminal** (from anywhere; the installed path works in any folder, or use the repo path):

| Command | Logs in to AKD? | What it does |
|---|---|---|
| `skills/akd-portfolio/run.sh` | **Yes**, once per account | Prints the portfolio as JSON |
| `skills/akd-portfolio/run.sh ticket sell --symbol MLCF --qty 500 --price 105.00 --held 1900 --ref 103.93 --account COAF00000` | No | Checks a **sell** ticket (order size, price sanity, enough shares). Exit 0 allowed, 1 blocked, 2 bad input |
| `skills/akd-portfolio/run.sh ticket buy --symbol OGDC --qty 100 --price 315.00 --free-cash 100000 --ref 315.87 --account COAF00000` | No | Checks a **buy** ticket (order size, price sanity, enough cash) |
| `python3 skills/akd-portfolio/hooks/install_hook.py` | No | Installs the approval hook (`--dry-run` to preview, `--uninstall` to remove) |
| `~/.claude/skills/akd-portfolio/run.sh` | **Yes** | Same as the first row, via the installed skill path |

Ticket flags (both): `--symbol`, `--qty`, `--price` (your limit, 2 decimals); optional `--account` (a label), `--ref` and `--ref-source` (AKD's price, as a typo guard). `ticket buy` also needs `--free-cash` (the account's free cash) and `ticket sell` also needs `--held` (shares of that stock the account holds). It's `ticket buy` / `ticket sell`, never a bare `buy` or `sell`: those would sound like placing a trade, and the hook blocks them. Limits: `AKD_TICKET_MAX_VALUE`, `AKD_TICKET_PRICE_BAND_PCT`, `AKD_TICKET_FEE_BUFFER_PCT` ([docs/CONFIGURATION.md](docs/CONFIGURATION.md)).

**For contributors** (run in the repo folder):

| Command | What it does |
|---|---|
| `make install` | Creates `.venv` and installs everything, including the dev tools |
| `make test` / `make lint` / `make check` | Tests (offline) / ruff / both together |
| `make fmt` | Auto-fixes lint issues |
| `make lock` | Regenerates `requirements.lock` after a dependency change |
| `make portfolio` | Same as `run.sh` (logs in) |
| `make test-live` | A **real** AKD login test; only with your own credentials, never in a loop |
| `make clean` / `make distclean` | Removes caches / also removes `.venv` |
| `make help` | Lists these | 

## Trade tickets (you place them yourself)

Ask Claude to turn a review into trades and it drafts tickets (stock, buy or sell, quantity, limit price). Each one is checked first: order size, price sanity against AKD's price, enough cash for a buy, enough shares for a sell. **Nothing is ever placed.** There is no order code and no PIN handling; you place the trade in AKD's own app. Limits are in [docs/CONFIGURATION.md](docs/CONFIGURATION.md).

## Approve every ticket (recommended)

An optional Claude Code hook stops each ticket command and shows you the order (side, stock, quantity, limit price, total, account) before anything runs. It also blocks any command that looks like order placement. Install it once:

```bash
python3 skills/akd-portfolio/hooks/install_hook.py          # add --dry-run to preview, --uninstall to remove
```

Then open `/hooks` in Claude Code (or start a new session) so it takes effect. It only adds its own entry to `~/.claude/settings.json` (a backup is made first). Without it, tickets still never place anything, you just don't get the approval prompt.

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
- [docs/CAPTURE-GUIDE.md](docs/CAPTURE-GUIDE.md): recording one real order (with your PIN hidden) to learn AKD's order message
