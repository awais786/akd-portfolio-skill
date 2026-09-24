# Project structure

```
akd-app/
├── CLAUDE.md                 guidance for Claude Code sessions in this repo
├── README.md                 what it is, quick start
├── pyproject.toml            deps, console script `akd-app`, pytest + ruff config
├── uv.lock                   pinned dependencies
├── .gitignore                ignores .venv, data/, caches
├── data/                     RUNTIME ONLY, git-ignored: akd.db, akd_creds.enc, akd_creds.key
│
├── src/akd/                  the application package
│   ├── __main__.py           entrypoint: `python -m akd` / `akd-app` (runs uvicorn)
│   ├── config.py             every setting, from AKD_* env vars, read once
│   ├── api.py                FastAPI app factory `create_app()`, all routes, serves the UI
│   ├── manager.py            AkdManager: builds the daemon, makes the ONE login attempt
│   ├── daemon.py             AkdDaemon: SOAP login + 5 s polling of the read operations
│   ├── supervisor.py         runs the daemon task; records crashes; bounded stop()
│   ├── store.py              SnapshotStore: append-only SQLite, two clocks, content-hash dedupe
│   ├── reads.py              snapshot -> API answer (freshness, null-vs-empty, flags)
│   ├── credentials.py        login in memory + Fernet-encrypted file
│   ├── parse.py              TradeCast CSV / order-history export parsers (import preview)
│   ├── public.key            AKD's RSA public key (Java-serialised), used for login encryption
│   ├── static/
│   │   └── index.html        the whole UI: one file, no build step
│   └── protocol/             PURE functions, no I/O
│       ├── soap.py           request builders, positional-login logic, response parsing
│       ├── crypto.py         reads public.key; RSA PKCS#1 v1.5 field encryption
│       ├── decode.py         GetCollateral / GetOrderHistory / GetExposureDynamic decoders
│       ├── models.py         AkdPosition, AkdOrder
│       ├── codec.py          trade-socket framing (XOR 0x1D)    [unused by the app]
│       └── session.py        trade-socket handshake/heartbeat   [unused by the app]
│
├── tests/                    no network, no real credentials
│   ├── conftest.py           isolates the data dir + credentials for every test; `store` fixture
│   ├── fixtures/             redacted captures
│   ├── test_soap.py  test_crypto.py  test_codec.py  test_session.py  test_decode.py   protocol layer
│   ├── test_daemon.py        login -> poll -> store, fake HTTP/socket (1 live test, skipped)
│   ├── test_store.py         two-clock semantics, dedupe, tie-breaking
│   ├── test_reads.py         freshness, no_snapshot / empty_book / payload_unreadable
│   └── test_api.py           endpoints via TestClient, error mapping
│
└── docs/
    ├── STRUCTURE.md          this file
    ├── ARCHITECTURE.md       data flow, data model, decisions
    ├── API.md                this app's HTTP endpoints
    ├── CONFIGURATION.md      env vars and files on disk
    ├── SECURITY.md           credentials, threat model, hardening
    ├── TROUBLESHOOTING.md    symptom -> cause -> fix
    ├── DEVELOPMENT.md        tests, lint, where to change what, known gaps
    ├── EXTRACTION.md         provenance from psx-platform, what changed/was dropped
    ├── PROTOCOL-MAP.md       AKD's own protocol (SOAP ops, sockets, login)
    └── PROTOCOL-RESEARCH.md  original reverse-engineering notes
```

## Layers and allowed dependencies

```
static/index.html ──HTTP──► api.py ──► reads.py ──► store.py
                              │
                              └──► manager.py ──► supervisor.py
                                        │
                                        └──► daemon.py ──► protocol/*  (pure)
                                                 │
                                                 ├──► credentials.py
                                                 └──► store.py
config.py is imported by everyone; it imports nothing from the package.
```

- `protocol/` imports nothing from the rest of the package (only stdlib and `cryptography`).
- `reads.py` never touches the network or the daemon; it only reads the store.
- `api.py` contains no business logic: it maps HTTP to `reads` / `manager`.
- Only `daemon.py` talks to AKD.

## Runtime data flow

1. `Connect` → `api.py` → `manager.connect()` → one `daemon.login()` → on success `supervisor.start()`.
2. Every 5 s the daemon polls 3 SOAP operations → `decode.py` → `store.put()`.
3. The browser polls `/api/akd/*` every 15 s → `reads.py` → `store.latest()`.
