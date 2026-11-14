# Troubleshooting

Run `make portfolio`. Errors are JSON on stdout with a non-zero exit code; diagnostics go to stderr.

| Symptom | Cause | Fix |
|---|---|---|
| `missing_credentials` (exit 2) | `AKD_USER_ID` or `AKD_PASSWORD` unset or blank | Export both in your shell, then rerun. |
| `login_rejected` (exit 3) | AKD refused the login (wrong account or password, locked account) | Check the credentials by logging in through AKD's own client. **Do not rerun in a loop**: repeated bad logins lock the account. |
| `akd_unreachable` (exit 4) | Network error, DNS, timeout, or AKD is down (it is reachable during PSX trading hours on weekdays) | Check your connection and try again later. |
| A section has `null` and `no_snapshot` | That read returned nothing decodable | See `poll` and `last_error` in the output. This means unknown, not "you own nothing". |
| `decoder_unverified` on orders | The order-history decoder is not yet checked against AKD's own record | Expected. Treat order rows as provisional. |
| `ModuleNotFoundError` / no `.venv` | Not installed | `make install`. |
