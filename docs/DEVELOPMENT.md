# Development

```bash
make install     # .venv + runtime and dev deps (pytest, ruff)
make test        # offline, no real credentials
make lint        # ruff
make check       # lint + test
make test-live   # REAL AKD login; needs AKD_ACCOUNT and AKD_PASSWORD; only when you mean it
```

Tests use fakes for HTTP (`tests/portfolio_fakes.py`, the daemon's injectable `http`/`transport`) and redacted fixtures (`tests/fixtures/`). They never touch the network.

## Adding a read operation

1. `protocol/soap.py`: request builder.
2. `protocol/decode.py`: decoder (skip bad rows, never raise; a non-blank body decoding to nothing is a failure).
3. `daemon.py`: a `_poll_*` method called from `poll_once`.
4. `reads.py`: an envelope function that returns `null` + `no_snapshot` when absent.
5. `portfolio.py`: add the section to `fetch_portfolio`'s result.
6. Tests for each layer, plus the password-leak scan in `tests/test_portfolio_cli.py`.

New settings go in `config.py`, `.env.example` and `docs/CONFIGURATION.md`. Dependencies change in `pyproject.toml`, `requirements.txt` and `requirements-dev.txt` together.
