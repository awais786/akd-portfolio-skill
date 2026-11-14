# Structure

```
akd-app/
├── README.md, CLAUDE.md, Makefile, pyproject.toml, requirements*.txt, .env.example
├── skills/akd-portfolio/SKILL.md   the Claude skill: when and how to run the command
├── src/akd/
│   ├── __main__.py        `python -m akd portfolio` entry point (no other subcommand)
│   ├── portfolio.py       fetch_portfolio (one login, one read pass) + run_portfolio (CLI, exit codes)
│   ├── daemon.py          AkdDaemon: SOAP login + read operations
│   ├── reads.py           snapshots -> honest envelopes (holdings, cash, orders)
│   ├── store.py           append-only SQLite snapshot store (two clocks)
│   ├── credentials.py     AKD login held IN MEMORY ONLY
│   ├── config.py          settings from AKD_* env vars
│   ├── public.key         AKD's RSA public key, used for login encryption
│   └── protocol/          pure protocol layer: soap, crypto, decode, models
├── tests/                 offline tests; fakes in portfolio_fakes.py; redacted fixtures in fixtures/
└── docs/                  CONFIGURATION, ARCHITECTURE, DEVELOPMENT, SECURITY, TROUBLESHOOTING, protocol notes
```
