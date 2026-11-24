# Structure

```
akd-app/
├── README.md, CLAUDE.md, Makefile, pyproject.toml, requirements*.txt, .env.example
├── skills/akd-sell-ticket/SKILL.md   /akd-sell-ticket: a sell ticket, checked against one account (uses akd-portfolio/run.sh)
├── skills/akd-buy-ticket/SKILL.md    /akd-buy-ticket: a buy ticket, checked against one account
├── skills/akd-portfolio/hooks/install_hook.py   installs/removes the approval hook in ~/.claude/settings.json (backup first)
├── skills/akd-portfolio/hooks/ticket_guard.py   PreToolUse hook: ticket commands need your approval, order shown; order placement denied
├── skills/akd-portfolio/SKILL.md   the Claude skill: when and how to run the command
├── src/akd/
│   ├── __main__.py        `python -m akd portfolio` entry point (no other subcommand)
│   ├── portfolio.py       fetch_portfolio (one login, one read pass) + run_portfolio (CLI, exit codes)
│   ├── tickets.py         trade-ticket validation and pre-trade checks (`python -m akd ticket`); never sends anything
│   ├── capture.py         redacting decoder for tcpdump recordings of the official TradeCast client (never prints a raw value)
│   ├── daemon.py          AkdDaemon: SOAP login + read operations
│   ├── reads.py           snapshots -> honest envelopes (holdings, cash, orders)
│   ├── store.py           append-only SQLite snapshot store (two clocks)
│   ├── credentials.py     AKD login held IN MEMORY ONLY
│   ├── config.py          settings from AKD_* env vars
│   ├── public.key         AKD's RSA public key, used for login encryption
│   └── protocol/          pure protocol layer: soap, crypto, codec, decode, models
├── tests/                 offline tests; fakes in portfolio_fakes.py; redacted fixtures in fixtures/
└── docs/                  CONFIGURATION, ARCHITECTURE, DEVELOPMENT, SECURITY, TROUBLESHOOTING, protocol notes
```
