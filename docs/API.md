# API

Base URL `http://127.0.0.1:8080`. JSON. **No authentication.** Interactive docs at `/docs` (Swagger) and `/openapi.json`.

## Envelope

Read endpoints return:

```json
{ "data": { ... }, "flags": ["..."], "retrieved_at": "2026-09-24T10:00:00+00:00", "staleness_seconds": 4.2 }
```

`retrieved_at` is the snapshot's `last_confirmed_at`, so `staleness_seconds` is about the *connection*, not how long the holdings have sat unchanged. Both are `null` when nothing was captured.

Every `data` from a read carries `broker_connected`, `as_of` (when content last changed) and `last_confirmed_at` (when AKD last confirmed it). **Check `broker_connected` before acting on any number.**

## Flags

| Flag | Meaning |
|---|---|
| `no_snapshot` | Nothing ever captured. The field is `null`, **not** "you own nothing". |
| `stale_snapshot` | AKD hasn't confirmed for > 15 min. `broker_connected` is false. |
| `clock_skew` | Snapshot is stamped > 5 min in the future. Never treated as live. |
| `empty_book` | Captured and decoded successfully to zero rows. |
| `payload_unreadable` | Stored payload has the wrong shape. Field is `null`, `broker_connected` false. |
| `decoder_unverified` | (orders) decoder not checked against the broker's record. Treat as provisional. |
| `no_positions_found` / `no_fills_found` | (import) nothing parsed. |

## Endpoints

### `GET /api/akd/holdings`
`data.positions`: `[{symbol, quantity, avg_cost, cost_value}]` (`avg_cost`/`cost_value` may be `null`), `data.totals`: `{position_count, total_cost_value, positions_without_cost}` (or `null`).

### `GET /api/akd/cash`
`data.cash`: `{cash_pkr, exposure_used_pkr, share_worth_pkr, equity_pkr, free_cash_pkr, pl_pkr}`. Any value can be `null`. `free_cash_pkr` is buying power.

### `GET /api/akd/orders`
`data.orders`: `[{order_id, symbol, side ("BUY"|"SELL"), quantity, price, status ("FILLED"), placed_at}]`. Executed fills only, not open orders.

### `GET /api/akd/status`
`data`: `{account_ref, operations_seen: {<op>: {last_seen, last_changed, snapshot_count}}, daemon: {name, state, error, authenticated, last_error}, broker_connected, as_of}`.
`daemon.state` is `running` / `stopped` / `crashed`. The UI uses this endpoint to decide between the login form and the connected view.

### `POST /api/akd/connect`
Body `{"user_id": "COAF00000", "password": "<full password>"}` (both required).
Performs one login. Success → `{"data": {"connected": true, "account": "..."}, "flags": []}` and the daemon starts. Failure clears the credentials and returns:

| Status | Meaning |
|---|---|
| 401 | AKD rejected the login. `detail` carries what AKD returned. An empty response usually means locked / rate-limited. |
| 502 | Could not reach AKD (network / DNS / timeout). |
| 422 | Missing field. |

### `POST /api/akd/disconnect`
Deletes stored credentials and stops the daemon. Captured snapshots stay in the database.

### `POST /api/import/positions` · `POST /api/import/orders`
Body `{"content": "<pasted text>"}`. **Preview only, nothing is stored.**
- positions: detects delimiter, header row and columns by keyword; returns `positions`, `skipped`, `detected_columns`, `warnings`. A row lacking quantity is skipped and reported, never invented.
- orders: parses `SYMBOL;QTY;PRICE;VALUE;SIDE;SETTLEMENT;TRADE_DATE;SETTLE_DATE;` records (pipe- or newline-separated) → `{fills, count}`.

### `GET /api/health` → `{"ok": true}` · `GET /` → the UI

## Example

```bash
curl -s localhost:8080/api/akd/holdings | jq '.data | {broker_connected, as_of, totals}'
curl -s -X POST localhost:8080/api/akd/connect -H 'content-type: application/json' \
     -d '{"user_id":"COAF00000","password":"..."}'
```
