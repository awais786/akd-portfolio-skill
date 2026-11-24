# Configuration

The `portfolio` command needs two environment variables. Export them in your shell; do not put the password in a file.

| Variable | Default | Meaning |
|---|---|---|
| `AKD_USER_ID` | unset (required) | Your first AKD account, e.g. `COAF00000`. `AKD_USER_ID_1` also works (not both). |
| `AKD_PASSWORD` | unset (required) | That account's full password. Held in memory for one run; never written, logged or printed. |
| `AKD_USER_ID_2`, `AKD_PASSWORD_2`, `_3`, ... | unset | More accounts. Slots are read in numeric order, one login attempt each; a failing account does not stop the others. An account listed twice is logged into once. |
| `AKD_SOAP_URL` | AKD's TradeCast SOAP endpoint | Change only if AKD moves it. |
| `AKD_DATA_DIR` | `data` | Where a `SnapshotStore` would live. `portfolio` uses a throwaway temp directory instead. |
| `AKD_TICKET_MAX_VALUE` | `100000` | Most PKR value of one order (shares x limit price). |
| `AKD_TICKET_MAX_QTY` | `10000` | Most shares in one order. |
| `AKD_TICKET_PRICE_BAND_PCT` | `10` | How far (%) a limit price may be from AKD's price (`--ref`) before it counts as a typo. |
| `AKD_TICKET_FEE_BUFFER_PCT` | `1` | Cash headroom (%) kept for fees and taxes when checking a BUY. `0` is allowed. |
| `AKD_LIMITS_FILE` | `limits.env` in the repo | The file the four limits above are read from. Set empty to turn the file off. |
| `AKD_ENV_FILE` | `.env` | A `.env` file to load. Real environment variables win over it. Set empty to disable. |

**The four `AKD_TICKET_*` limits are variables**, read in this order: the environment, then `limits.env`, then the defaults. Copy `limits.env.example` to `limits.env` (git-ignored) and edit it; it is not a secret file, so keep passwords out of it. Both the ticket check (`python -m akd ticket`) and the approval hook (`ticket_guard.py`) read them, and the hook **denies** a ticket that breaks a limit before it runs. Only the `AKD_TICKET_*` lines of that file are ever read, and the hook never opens `.env`. A value that is missing, not a number, infinite or not above zero falls back to the default, never to "no limit".

`.env` is optional and git-ignored; `.env.example` shows the format. **Never put `AKD_PASSWORD` in it.**

Constants in `config.py` (not environment-tunable): `LIVE_WINDOW_MINUTES = 15`, `CLOCK_SKEW_TOLERANCE_MINUTES = 5`.
