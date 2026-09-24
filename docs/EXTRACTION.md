# Extraction notes: from `psx-platform`

This app is a **copy**; `psx-platform` was not modified. The AKD code there was spread over five places and wired through the platform's tool registry, plugin kernel, MCP server, passkey auth and Next.js app. This project keeps the working core and removes that scaffolding.

## Provenance

| Here | Came from (psx-platform) | Change |
|---|---|---|
| `src/akd/protocol/*` , `public.key` | `brokers/akd/protocol/*`, `brokers/akd/public.key` | none (verbatim) |
| `src/akd/daemon.py` | `brokers/akd/daemon/service.py` | imports flattened; endpoint constants moved to `config.py`; `PSX_AKD_*` → `AKD_*`; manifest dependency removed |
| `src/akd/store.py` | `packages/data/src/psx_data/store/broker.py` + `brokers/akd/store.py` | merged; `broker` column dropped (single broker); migration for pre-`last_confirmed_at` DBs dropped (new DB) |
| `src/akd/credentials.py` | `brokers/akd/runtime.py` | plugin registry removed; paths from `AKD_DATA_DIR` |
| `src/akd/supervisor.py` | `packages/plugins/.../supervisor.py` | verbatim |
| `src/akd/reads.py` | `brokers/akd/tools/akd.py` | registry/`Principal`/`@tool` removed; same freshness and null-vs-empty logic; PSX valuation removed; cash now reads `GetExposureDynamic` |
| `src/akd/parse.py` | `packages/trading/.../akd/parse.py` | verbatim |
| `src/akd/api.py`, `manager.py` | `brokers/akd/plugin.py`, tool routes, `apps/api/.../auth_routes.py` import routes | rewritten as a small FastAPI app; import routes now preview-only |
| `src/akd/static/index.html` | `apps/web/**` AKD pages/components (holdings, orders, cash, connect, import) | rewritten as one static page |
| `tests/*` | `brokers/akd/tests/*` | imports renamed; new store/reads/api tests |
| `docs/PROTOCOL-*.md` | `docs/AKD-PROTOCOL.md`, `docs/akd-protocol/…protocol-map.md` | verbatim |

## Removed on purpose

| Dropped | Why |
|---|---|
| Tool registry, MCP server, `Principal`/scopes | One user, one process. |
| Passkey/WebAuthn login, `PSX_LOCAL_MODE` | Replaced by "loopback only". |
| Portfolio ledger (`import/akd` created portfolios & transactions) | Depended on the platform's accounts DB. Imports are now previews. |
| PSX market data and mark-to-market valuation | Needs the platform's PSX source (which itself was getting 403s). Values shown at cost. |
| Next.js/React/Tailwind app | One HTML file does the job. |
| Postgres/Redis/NATS/Meilisearch/MinIO compose stack | Never needed by AKD; SQLite. |
| `import-linter` layering, uv workspace | Single package. |

## Behavioural differences to be aware of

1. **Cash works.** The original `psx_akd_cash` read a `GetAccountStatement` snapshot that nothing wrote; it always said `no_snapshot`. Here it reads `GetExposureDynamic`, which the daemon does store.
2. **Environment variables renamed** `PSX_AKD_*`/`PSX_VAR_DIR` → `AKD_*`. Saved credentials and the database are **not** compatible or shared with psx-platform's `var/` directory. You log in once here.
3. **No `Principal`.** Snapshots are keyed by `AKD_ACCOUNT_REF` (default `local`) instead of the platform's `local-owner`.
4. **Endpoints unchanged** (`/api/akd/holdings|cash|orders|status|connect|disconnect`), but the response is a simplified envelope (`data`, `flags`, `retrieved_at`, `staleness_seconds`) and there is no `valuation` block.

## Still in psx-platform

Nothing was deleted there. If you later want AKD gone from the monorepo, that is a separate change: remove `brokers/akd`, `psx_trading/akd`, the AKD registry tools, the two import routes in `auth_routes.py`, the AKD web pages, and the related import-linter contracts and tests (`tests/test_akd_deletion_safety.py`, `test_layering_brokers.py`).
