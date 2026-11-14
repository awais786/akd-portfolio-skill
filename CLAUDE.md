# CLAUDE.md

Read-only **AKD TradeCast portfolio fetch**, packaged as a Claude skill (`skills/akd-portfolio/`). `python -m akd portfolio` logs in to each configured AKD account once (`AKD_USER_ID` / `AKD_PASSWORD`, then `AKD_USER_ID_2` / `AKD_PASSWORD_2`, ...) from the environment, reads holdings, cash and order history once, and prints one JSON document `{"accounts": [...]}`. Pure Python, no server, no database server. Layout: `docs/STRUCTURE.md`.

## Commands (use the Makefile)

```bash
make install        # .venv + runtime and dev dependencies
make portfolio      # run the fetch (needs AKD_USER_ID and AKD_PASSWORD exported in the shell)
make test           # offline, no real credentials
make lint           # ruff, must stay clean
make check          # lint + test: run before finishing any change
make clean          # caches;  make distclean = also remove .venv
```

- Always run code through `.venv` (the Makefile does). Never `pip install` into the system Python.
- **Dependencies live in three files**: `pyproject.toml`, `requirements.txt`, `requirements-dev.txt`. Change all three together.
- Don't run `make test-live` or `make portfolio` unless the user supplies real credentials and asks.

## Rules that matter (do not break these)

1. **Nothing is ever sent to AKD beyond the read operations.** No order entry exists. Never implement one from guesses (AKD's order message has never been observed), never store or log a PIN.
2. **Absence is never emptiness.** No data captured -> `null` + `no_snapshot`, never `[]`/`{}`/`0`. A non-blank body that decodes to zero rows is a decode failure and must NOT be stored as an empty book. Keep `reads.py` and the daemon's poll guards consistent.
3. **Two clocks.** `captured_at` = content last changed; `last_confirmed_at` = AKD last re-sent it. Liveness comes from `last_confirmed_at` only.
4. **One login attempt per account per run; no retry on rejection** (a failed account never stops the next one, and a repeated account id is logged into once). AKD locks accounts. Never add retry loops or auto-reconnect around a rejected login.
5. **The AKD password is in memory only.** Never write it to disk in any form, never log or return it, never put it in an exception, test output, or the skill text. Only 4 positional characters (RSA-encrypted) go on the wire. Credentials come from the environment, never from `.env`. The tests in `tests/test_portfolio_cli.py` scan stdout, stderr and logs for the password; keep them passing.
6. **`protocol/` stays pure** (no I/O, no clock; `as_of` passed in). Decoders skip a bad row, never raise.
7. Never log a login response when `valid` (it holds the session token). The `portfolio` command keeps the `akd` logger at WARNING so the daemon's login diagnostic (account id, password length) is not emitted.
8. `broker_connected`-style liveness is judged from `last_confirmed_at`, not from data merely being present.

## Conventions

- Config is read once in `config.py` from `AKD_*` env vars. Add new settings there and in `docs/CONFIGURATION.md`.
- Tests use no network and no real credentials. `tests/conftest.py` redirects the data dir per test; keep it autouse. The daemon takes injectable `http`/`transport`, so use fakes (`tests/portfolio_fakes.py`).
- Fixtures under `tests/fixtures/` are redacted. Never add real account data, tokens or captures.
- Match existing style: type hints, `from __future__ import annotations`, ruff rules E/F/I/UP/B.
- New AKD read operation: `soap.py` builder -> `decode.py` -> `daemon.py` poll -> `reads.py` -> `portfolio.py` output (see `docs/DEVELOPMENT.md`).
- Don't commit unless asked. `.venv/` and `.env` are git-ignored.

## Known gaps (don't "fix" silently)

- `GetAccountStatement` has a builder but no decoder/poller.
- `decode_order_history` is unverified against ground truth (output flags `decoder_unverified`).
- Market price is AKD's own `MTM` rate as of the last confirmed snapshot, not a live quote. P/L per share/total/% are derived in `reads._with_pl` and must stay `None` (never 0) when an input is missing.
