# Architecture

One short-lived process. No server, no long-running task.

```
python -m akd portfolio
  __main__.main ──► portfolio.run_portfolio(env, stdout, stderr)
                      └─ for each account (accounts_from_env): asyncio.run(fetch_portfolio(user, password))
                           ├─ AkdDaemon.login()      ONE SOAP login (RSA-encrypted positional password)
                           ├─ AkdDaemon.poll_once()  GetCollateral, GetExposureDynamic, GetOrderHistory
                           │     decoders in protocol/decode.py ──► throwaway SnapshotStore (temp dir)
                           └─ reads.holdings / cash / orders ──► envelopes ──► one JSON document
```

## Rules the design protects

- **One login attempt.** A rejected login raises `login_rejected` and nothing else is sent.
- **Absence is never emptiness.** `reads.*` return `null` + `no_snapshot` when nothing was stored; a non-blank body that decodes to zero rows is a failed poll, not an empty book.
- **Two clocks.** `captured_at` (content last changed) vs `last_confirmed_at` (AKD last re-sent it); freshness uses the second.
- **Memory-only password.** `credentials.py` holds it for the run; the snapshot store lives in a temp directory that is removed on exit and never contains a password.
- **Pure protocol layer.** `protocol/` does no I/O and reads no clock.

Error codes map to exit codes: `missing_credentials` 2, `login_rejected` 3, `akd_unreachable` 4.
