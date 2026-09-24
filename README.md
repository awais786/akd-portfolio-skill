# AKD App

A small, standalone, **read-only** viewer for your AKD TradeCast brokerage account: holdings, cash / buying power, and executed order history, in one web page.

It was extracted from the `psx-platform` monorepo so it runs by itself: one Python package, one process, one SQLite file, one HTML page. **No Node, no Next.js, no Django, no Docker, no database server.**

> **Read-only, by design.** It logs in and reads. It cannot place, modify or cancel orders. The trade-socket write path was never built.
> **Unofficial.** It speaks TradeCast's SOAP protocol as reverse-engineered from the client, not a supported public API. AKD can change it at any time. See [docs/SECURITY.md](docs/SECURITY.md) before using it with a real account.

## Quick start

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
cd akd-app
uv sync                 # installs deps into .venv
uv run akd-app          # serves http://127.0.0.1:8080
```

Open <http://127.0.0.1:8080>, enter your AKD account (e.g. `COAF00000`) and your **full** password, and click **Connect**. Within a few seconds the Holdings, Cash and Orders tabs fill in.

Change the port with `AKD_PORT=9000 uv run akd-app`. All settings: [docs/CONFIGURATION.md](docs/CONFIGURATION.md).

## What you get

| Tab | Source (SOAP operation) | Shows |
|---|---|---|
| Holdings | `GetCollateral` | symbol, quantity, average cost, cost value, totals |
| Cash | `GetExposureDynamic` | cash, exposure used, share worth, equity, **free cash (buying power)**, P/L |
| Orders | `GetOrderHistory` | executed fills: date, symbol, side, quantity, price (**provisional**, see below) |
| Import | local parser | paste a TradeCast CSV / order-history export to preview how it parses (nothing stored) |

The daemon re-polls AKD every 5 seconds while connected. A saved (encrypted) session is restored on restart.

## Things worth knowing

- **Honest "no data".** If nothing was ever captured, fields are `null` with a `no_snapshot` flag, never `[]` or `0`, because an empty list would claim "you own nothing".
- **Two timestamps.** `as_of` is when the data last *changed*; `last_confirmed_at` is when AKD last *re-sent* it. Old `as_of` on a live connection just means nothing changed. "Connected" is judged from `last_confirmed_at` (live = within 15 minutes).
- **No market prices.** Values are at **cost**, not marked to market. The original platform marked to PSX prices, which needs a PSX data source this app deliberately doesn't carry.
- **Order history is unverified.** The decoder works on real captures but has never been checked against the broker's own record of fills. The UI and API say so.
- **Account lockout.** Connecting makes exactly one login attempt. A rejected login stops everything and never retries. A wrong password costs one attempt, not a lockout spiral. Don't script rapid reconnects.
- **No login on the app itself.** It binds to `127.0.0.1` only. Don't expose it. See [docs/SECURITY.md](docs/SECURITY.md).

## Documentation

| Doc | Contents |
|---|---|
| [docs/STRUCTURE.md](docs/STRUCTURE.md) | annotated file tree, layers, dependency rules |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | modules, data flow, data model, design decisions |
| [docs/API.md](docs/API.md) | every endpoint, envelope, flags, examples |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | every environment variable, files on disk |
| [docs/SECURITY.md](docs/SECURITY.md) | credential handling, threat model, hardening |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | login rejected, blank tabs, stale data, port in use |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | tests, lint, layout, how to extend |
| [docs/EXTRACTION.md](docs/EXTRACTION.md) | what came from psx-platform, what changed, what was dropped |
| [docs/PROTOCOL-MAP.md](docs/PROTOCOL-MAP.md), [docs/PROTOCOL-RESEARCH.md](docs/PROTOCOL-RESEARCH.md) | the reverse-engineered TradeCast protocol (original research) |

## Layout

```
src/akd/
  api.py          FastAPI app + routes, serves the UI
  manager.py      builds/supervises the daemon; the single login attempt
  daemon.py       SOAP login + 5s read polling (+ optional trade socket)
  reads.py        turns stored snapshots into honest API answers
  store.py        append-only SQLite snapshot store (two clocks)
  credentials.py  in-memory + encrypted-on-disk login
  parse.py        TradeCast export parsers (import preview)
  protocol/       pure SOAP / crypto / codec / decoders
  static/index.html   the entire UI
tests/            98 tests, no network, no real credentials
```
