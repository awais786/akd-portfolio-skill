# Troubleshooting

Start with `curl -s localhost:8080/api/akd/status | jq` and the server log (the terminal running `akd-app`).

| Symptom | Cause | Fix |
|---|---|---|
| **401 "AKD login rejected - …"** | Wrong account/password, or you typed the 4 challenge characters. | Enter the **complete** password. Retry once; do not hammer. |
| Rejected with **"HTTP 200, completely empty response - likely locked/rate-limited"** | AKD locked the account or is throttling you after failed attempts. | Wait, then log into AKD's own site to confirm the account works; contact AKD if locked. |
| **502 "could not reach AKD"** | No network, DNS failure, AKD down, or a firewall blocks `online.akdtrade.biz` (HTTP port 80). | Check connectivity; try `curl -I http://online.akdtrade.biz`. Override with `AKD_SOAP_URL` if AKD moved. |
| Connected, but tabs say "No … captured yet" | The first poll hasn't landed (up to ~5 s), or every poll is failing to decode. | Watch `daemon.last_error` in `/api/akd/status`. |
| `last_error`: **"GetCollateral: non-blank body decoded to 0 positions - not stored"** | Session expired or AKD changed the response format. The app refuses to store this rather than claim "empty book". | Disconnect and reconnect. If persistent, AKD changed format; compare with a fresh capture (see PROTOCOL-MAP.md). |
| `last_error`: "no `<return>` element" | AKD returned a SOAP fault or HTML. | Reconnect; check the log line before it. |
| Status shows `daemon.state: "crashed"` | A network error interrupted the login call itself. | Click Connect again. |
| `stale_snapshot` / **Not connected** while it worked earlier | AKD hasn't confirmed data for > 15 min: daemon stopped, laptop slept, session dropped. | Reload; if still stale, Disconnect → Connect. |
| **Old "as_of" but "Connected"** | Normal: nothing changed. `as_of` is when content last changed. | none |
| Orders tab shows a `decoder_unverified` banner | Intentional; see README. | Cross-check a few fills against AKD's own statement. |
| Cash tab empty but Holdings works | `GetExposureDynamic` decoded to nothing usable (unrecognised summary line). | Check `last_error`; the response format may differ for your account type. |
| **Restarted app, has to log in again** | Saved session missing/undecryptable (different `AKD_DATA_DIR`, deleted key, or previous login was rejected). | Connect again. |
| **"address already in use"** | Something else owns port 8080. | `AKD_PORT=8090 uv run akd-app`. |
| Import tab: "no valid positions found" | No Symbol + Quantity header recognised. | Include the header row; the parser matches keywords like *symbol/scrip*, *qty/holding/quantity*, *avg cost/rate*. |

## Getting more detail

- Logs: login attempts log a diagnostic (URL, user, password *length*, positions, response body up to 800 chars). No secret characters.
- Start with `--log-level`: run `uv run uvicorn akd.api:app --log-level debug`.
- Reset all state: stop the app, delete `data/`, start again.
