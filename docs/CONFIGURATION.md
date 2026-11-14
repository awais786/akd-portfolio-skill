# Configuration

The `portfolio` command needs two environment variables. Export them in your shell; do not put the password in a file.

| Variable | Default | Meaning |
|---|---|---|
| `AKD_USER_ID` | unset (required) | Your first AKD account, e.g. `COAF00000`. `AKD_USER_ID_1` also works (not both). |
| `AKD_PASSWORD` | unset (required) | That account's full password. Held in memory for one run; never written, logged or printed. |
| `AKD_USER_ID_2`, `AKD_PASSWORD_2`, `_3`, ... | unset | More accounts. Slots are read in numeric order, one login attempt each; a failing account does not stop the others. An account listed twice is logged into once. |
| `AKD_SOAP_URL` | AKD's TradeCast SOAP endpoint | Change only if AKD moves it. |
| `AKD_DATA_DIR` | `data` | Where a `SnapshotStore` would live. `portfolio` uses a throwaway temp directory instead. |
| `AKD_ENV_FILE` | `.env` | A `.env` file to load. Real environment variables win over it. Set empty to disable. |

`.env` is optional and git-ignored; `.env.example` shows the format. **Never put `AKD_PASSWORD` in it.**

Constants in `config.py` (not environment-tunable): `LIVE_WINDOW_MINUTES = 15`, `CLOCK_SKEW_TOLERANCE_MINUTES = 5`.
