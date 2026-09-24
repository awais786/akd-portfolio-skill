# CLAUDE.md

Standalone, **read-only** viewer for an AKD TradeCast brokerage account (holdings, cash, order history). Pure Python (FastAPI + SQLite) with one static HTML page. No Node, JVM, Django or Docker. Extracted from `psx-platform`; see `docs/EXTRACTION.md`. The layout is in `docs/STRUCTURE.md`.

## Commands

```bash
uv sync                         # install (incl. dev group)
uv run akd-app                  # run on http://127.0.0.1:8080  (AKD_PORT to change)
uv run pytest -q                # 98 pass, 1 skipped (live test)
uv run ruff check src tests     # must stay clean
uv run uvicorn akd.api:app --reload --port 8080   # dev server
```

## Rules that matter (do not break these)

1. **Read-only.** Never add order placement, modification or cancellation. The trade socket is deliberately unused.
2. **Absence is never emptiness.** No data captured → `null` + `no_snapshot`, never `[]`/`{}`/`0`. A non-blank body that decodes to zero rows is a decode failure and must NOT be stored as an empty book. Keep `reads.py` and the daemon's poll guards consistent.
3. **Two clocks.** `captured_at` = content last changed; `last_confirmed_at` = AKD last re-sent it. Liveness and `staleness_seconds` come from `last_confirmed_at` only.
4. **One login attempt per Connect; no retry on rejection.** AKD locks accounts. Never add retry loops or auto-reconnect around a rejected login.
5. **Never log, return or store in plaintext the password.** Not in responses, logs, exceptions or tests. Only 4 positional characters (RSA-encrypted) go on the wire.
6. **`protocol/` stays pure** (no I/O, no clock; `as_of` passed in). Decoders skip a bad row, never raise.
7. **UI renders with `textContent` only**, never `innerHTML`. Broker strings are untrusted.
8. **Loopback only, no auth.** Don't add `0.0.0.0` defaults or CORS headers.
9. **`broker_connected` in status requires the daemon to be running**, not just fresh data.

## Conventions

- Config is read once in `config.py` from `AKD_*` env vars. Add new settings there and in `docs/CONFIGURATION.md`.
- Tests use no network and no real credentials. `tests/conftest.py` redirects the data dir per test; keep it autouse. The daemon takes injectable `http`/`transport`, so use fakes.
- Fixtures under `tests/fixtures/` are redacted. Never add real account data, tokens or captures.
- Match existing style: type hints, `from __future__ import annotations`, ruff (line length ignored, rules E/F/I/UP/B).
- New API endpoints: route in `api.py` (thin), logic in `reads.py`/`manager.py`, docs in `docs/API.md`, test in `tests/test_api.py`.
- New AKD read operation: `soap.py` builder → `decode.py` → `daemon.py` poll → `reads.py` → `api.py` → `static/index.html` (see `docs/DEVELOPMENT.md`).

## Known gaps (don't "fix" silently)

- `GetAccountStatement` has a builder but no decoder/poller.
- `decode_order_history` is unverified against ground truth (API flags `decoder_unverified`).
- No mark-to-market prices; values are at cost.
- Single account only (`AKD_ACCOUNT_REF`).

## Local state

`data/` (git-ignored) holds `akd.db`, `akd_creds.enc`, `akd_creds.key`. Never commit or print its contents. Don't run `pytest -m live` unless the user supplies real credentials and asks.
