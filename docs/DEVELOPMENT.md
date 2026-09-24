# Development

```bash
uv sync                    # deps incl. dev group (pytest, ruff)
uv run pytest -q           # 98 pass, 1 skipped (live test)
uv run ruff check src tests
uv run uvicorn akd.api:app --reload --port 8080
```

## Tests

- **No network, no real credentials, no real account data.** `tests/conftest.py` redirects the data dir to a temp path for every test and clears in-memory credentials, so a run can never touch a real `./data` or saved session.
- `test_soap / test_crypto / test_codec / test_session / test_decode`: protocol layer, against redacted fixtures in `tests/fixtures/`.
- `test_daemon`: login → poll → store, with a fake HTTP client and fake socket transport; reconnect/backoff/stop behaviour.
- `test_store`: two-clock semantics, dedupe, tie-breaking.
- `test_reads`: freshness, `no_snapshot`/`empty_book`/`payload_unreadable`, clock skew.
- `test_api`: endpoints via `TestClient` with an injected temp store; error mapping; password never echoed.
- `test_daemon.py::test_live_login_round_trip` is `@pytest.mark.live`, skipped unless you run `pytest -m live` **with real credentials in `AKD_ACCOUNT` and `AKD_PASSWORD`** (read its docstring first, since it logs into the real broker).

## Where to change what

| I want to… | Edit |
|---|---|
| add a new read operation | `soap.py` (request builder) → `decode.py` (decoder) → `daemon.py` (`poll_once` + a `_poll_*`) → `reads.py` (view) → `api.py` (route) → `static/index.html` |
| change what counts as "live" | `config.py` `LIVE_WINDOW_MINUTES` |
| change poll rate | `daemon.POLL_SECONDS` (be gentle; AKD locks accounts) |
| mark holdings to market | add a price source and extend `reads.holdings`; keep prices out of the store (they're not AKD facts) |
| second account | run a second instance with its own `AKD_DATA_DIR` and `AKD_PORT` |

## Conventions kept from the original

- Decoders are pure: text + explicit `as_of` in, typed records out. No clock, no I/O.
- A malformed row is skipped, never raised; a whole-body failure is never stored as an empty book.
- `null` means "no evidence"; `[]` means "captured and empty".
- Network is injectable in the daemon; tests never open a socket.

## Known gaps

- `GetAccountStatement` (the running cash ledger) has a request builder but no decoder or poller.
- `decode_order_history` output is unverified against ground truth.
- The trade socket handshake's server ack was never observed; the socket is not used.
- No order entry, and none planned here.
- `python -m akd` runs a single uvicorn worker; multiple workers would each start their own daemon.
