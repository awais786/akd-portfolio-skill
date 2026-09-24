# Configuration

All via environment variables, read once at startup (`src/akd/config.py`). Nothing is required; defaults work.

| Variable | Default | Purpose |
|---|---|---|
| `AKD_HOST` | `127.0.0.1` | Bind address. Anything non-loopback logs a warning: the app has **no login**. |
| `AKD_PORT` | `8080` | HTTP port. |
| `AKD_DATA_DIR` | `./data` | Directory for the database and encrypted credentials. Relative paths resolve against the working directory. |
| `AKD_ACCOUNT_REF` | `local` | Key under which snapshots are stored. Only change to keep separate accounts in separate data dirs. |
| `AKD_PASSWORD` | unset | Headless fallback password (CI / no browser). Used only if no password was set through the UI. |
| `AKD_SOAP_URL` | `http://online.akdtrade.biz/TradeCastServicev428/LoginServerService` | AKD SOAP endpoint. |
| `AKD_SOCKET_HOST` / `AKD_SOCKET_PORT` | `online.akdtrade.biz` / `8092` | Trade socket. Unused: the app never opens it. |

Constants in code (not env): poll interval 5 s (`daemon.POLL_SECONDS`), live window 15 min and clock-skew tolerance 5 min (`config.py`), server code `03`.

## Files created in `AKD_DATA_DIR`

| File | Contents | Sensitivity |
|---|---|---|
| `akd.db` (+ `-wal`, `-shm`) | your holdings, order history, cash figures | **Private financial data** |
| `akd_creds.enc` | Fernet-encrypted `{user_id, password}` | **Secret**, together with the key |
| `akd_creds.key` | the Fernet key (mode 0600) | **Secret** |

Delete `akd_creds.enc` (or click **Disconnect**) to forget the login. Delete the whole directory to reset everything. The directory is git-ignored.

## Running

```bash
uv run akd-app                                   # console script
uv run python -m akd                             # same
uv run uvicorn akd.api:app --port 8080 --reload  # dev
AKD_PORT=9000 AKD_DATA_DIR=~/.akd uv run akd-app
```
