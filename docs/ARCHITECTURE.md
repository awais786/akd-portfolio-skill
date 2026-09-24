# Architecture

One process. FastAPI serves a JSON API and one static HTML page; a background asyncio task (the *daemon*) logs into AKD and polls; everything lands in one SQLite file.

```
Browser (static/index.html)
   │  fetch /api/akd/*         every 15 s
   ▼
FastAPI  (api.py)  ── reads.py ──► SnapshotStore (SQLite, data/akd.db)
   │                                      ▲
   │ connect / disconnect                 │ put()  every 5 s
   ▼                                      │
AkdManager (manager.py) ─► DaemonSupervisor ─► AkdDaemon (daemon.py)
                                             │  SOAP over HTTP
                                             ▼
                                   AKD TradeCast (online.akdtrade.biz)
```

## Modules

| Module | Responsibility |
|---|---|
| `config.py` | All settings, read from env once (see CONFIGURATION.md). |
| `api.py` | `create_app(store=None)` factory. Lifespan builds the store and manager, restores a saved session, and stops the daemon on shutdown. Routes only translate HTTP to `reads`/`manager`. |
| `manager.py` | Owns the one daemon. `connect()` performs **exactly one** login and only then starts the poll loop. Raises `LoginRejected` (→ 401) or `AkdUnreachable` (→ 502). |
| `daemon.py` | `AkdDaemon`: SOAP `SecurecustomAuthentication` login, then every 5 s polls `GetCollateral`, `GetOrderHistory`, `GetExposureDynamic` and stores each. Network is injectable (`http`, `transport`) so it is fully testable offline. The trade socket is only opened if a `transport` is injected. The app never does. |
| `supervisor.py` | Runs the daemon coroutine, records a crash as state instead of propagating, and bounds `stop()` to 5 s. |
| `store.py` | `SnapshotStore`: append-only, content-hash-deduped, two-clock snapshots. |
| `reads.py` | Pure functions: stored snapshot → response envelope with flags. All freshness/absence logic lives here. |
| `credentials.py` | Login held in memory, mirrored to a Fernet-encrypted file. |
| `parse.py` | Tolerant parsers for TradeCast CSV exports and raw order-history text. |
| `protocol/` | `soap.py` (request builders, login-position logic, response parsing), `crypto.py` (Java-serialised RSA public key → PKCS#1 v1.5 field encryption), `decode.py` (collateral / order-history / exposure decoders), `codec.py` + `session.py` (trade-socket framing, unused by the app). Pure, no I/O. |

## Login

AKD's web-era login is *positional*. The server challenges for 4 password characters at 4 positions. The daemon picks 4 distinct random positions (`soap.choose_login_positions`), sends only those characters and the positions, each RSA-encrypted with the key in `public.key`, and never sends the full password. The response's `valid` flag is the **only** authoritative "logged in" signal; the returned token becomes the SOAP `user_id` for later calls.

A rejected login ends `AkdDaemon.run()` immediately with no retry. Repeatedly re-authenticating against credentials the broker just refused is what trips account-lock defences. A *transient* failure (network error) raises out of `run()` and shows as `crashed` in status. Recovery in both cases is a manual reconnect.

## Data model

One table:

```sql
snapshots(id, account_ref, operation, captured_at, last_confirmed_at, content_hash, payload,
          UNIQUE(account_ref, operation, content_hash))
```

- `operation` ∈ `GetCollateral`, `GetOrderHistory`, `GetExposureDynamic`.
- `payload` is canonical JSON of the **decoded** result (sorted keys), so wire whitespace or ordering never reads as a change.
- `put()` inserts a new row when content is new. If identical content exists it advances that row's `last_confirmed_at` (never backwards) and returns `False`.
- `latest()` orders by `last_confirmed_at DESC, id DESC`.

Why two clocks: an unchanged book is normal. Judging liveness from when the content *first appeared* would call a healthy connection stale within 15 minutes. Advancing `last_confirmed_at` on collision also fixes two real defects of the single-clock design: *sell then rebuy* (A→B→A left B as "latest") and *transient blank then real book* (the real book collided with its earlier row and was rejected).

History is retained: every distinct book is a row. Nothing is ever updated in place except `last_confirmed_at`.

## "Absence is never emptiness"

Enforced in two places:

1. **Daemon:** a non-blank body that decodes to zero rows is treated as a *decode failure* (`last_error`, not stored). Only a genuinely blank body (or the order-history `No Record` sentinel) stores an empty book.
2. **reads.py:** nothing captured → field `null` + `no_snapshot`; payload wrong shape → `null` + `payload_unreadable` + `broker_connected: false`; decoded-to-zero → `[]` + `empty_book`.

## Decisions

- **Static HTML, not a framework.** The UI is 4 tables and a login form. All API data is rendered via `textContent`, never `innerHTML`, so broker-supplied strings cannot inject markup.
- **SQLite, not Postgres.** Single user, single writer, tiny data.
- **Single account.** The AKD protocol carries no account identifier, so everything is stored under `AKD_ACCOUNT_REF` (default `local`). Two AKD accounts would need two data directories/instances.
- **Cost value only.** No live prices; see README.
- **Cash reads `GetExposureDynamic`.** That is what the daemon actually stores. (The original platform's cash tool read a `GetAccountStatement` snapshot that nothing ever wrote, so it always reported `no_snapshot`. Fixed here.)
