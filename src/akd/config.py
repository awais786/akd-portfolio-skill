"""All configuration, resolved once, from environment variables.

Nothing here is a secret. The AKD password is never read from the environment
unless you explicitly set AKD_PASSWORD for headless use (see credentials.py).
"""
from __future__ import annotations

import os
from pathlib import Path

# Where the SQLite database and the encrypted credential file live.
DATA_DIR = Path(os.environ.get("AKD_DATA_DIR", "data")).expanduser().resolve()
DB_PATH = DATA_DIR / "akd.db"

# One local user. The AKD wire protocol carries no account id, so every
# snapshot is stored under this single reference.
ACCOUNT_REF = os.environ.get("AKD_ACCOUNT_REF", "local")

# Web server. Loopback by default - the app has NO login of its own.
HOST = os.environ.get("AKD_HOST", "127.0.0.1")
PORT = int(os.environ.get("AKD_PORT", "8080"))

# AKD endpoints (from the protocol research; see docs/PROTOCOL-MAP.md).
SOAP_URL = os.environ.get(
    "AKD_SOAP_URL",
    "http://online.akdtrade.biz/TradeCastServicev428/LoginServerService",
)
SOCKET_HOST = os.environ.get("AKD_SOCKET_HOST", "online.akdtrade.biz")
SOCKET_PORT = int(os.environ.get("AKD_SOCKET_PORT", "8092"))
SERVER_CODE = "03"  # protocol map §2

# How long after AKD last confirmed a snapshot we still call the connection live.
LIVE_WINDOW_MINUTES = 15
# A snapshot stamped further in the future than this is broken, not fresh.
CLOCK_SKEW_TOLERANCE_MINUTES = 5


def ensure_data_dir() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR
