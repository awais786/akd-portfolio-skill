"""All configuration, resolved once, from environment variables and an optional `.env` file.

Precedence: real environment variables > `.env` > the defaults below.
`.env` may hold the AKD password in plain text (see docs/SECURITY.md), so loading it warns on stderr when
other users can read the file. The password itself is only used by credentials.py.
"""
from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path


def load_env_file(path: str | Path) -> dict[str, str]:
    """Parse simple `KEY=VALUE` lines (comments and blanks skipped, one pair of
    surrounding quotes stripped) and add them to `os.environ` WITHOUT overriding
    variables that are already set. Returns what was loaded. A missing or
    unreadable file is not an error."""
    loaded: dict[str, str] = {}
    file = Path(path).expanduser()
    try:
        text = file.read_text()
        group_or_world = file.stat().st_mode & 0o077
    except OSError:
        return loaded
    if group_or_world:
        print(f"akd: {file.name} can be read by other users; run `chmod 600 {file.name}` (it can hold your password).",
              file=sys.stderr)
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip().removeprefix("export ").strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = loaded[key] = value
    return loaded


# AKD_ENV_FILE selects another file; set it to an empty string to disable .env loading.
_env_file = os.environ.get("AKD_ENV_FILE", ".env")
if _env_file:
    load_env_file(_env_file)

# Where the throwaway SnapshotStore would live (the portfolio command uses a temp dir instead).
DATA_DIR = Path(os.environ.get("AKD_DATA_DIR", "data")).expanduser().resolve()
DB_PATH = DATA_DIR / "akd.db"

# AKD endpoints (from the protocol research; see docs/PROTOCOL-MAP.md).
SOAP_URL = os.environ.get(
    "AKD_SOAP_URL",
    "http://online.akdtrade.biz/TradeCastServicev428/LoginServerService",
)
SERVER_CODE = "03"  # protocol map §2

# How long after AKD last confirmed a snapshot we still call the connection live.
LIVE_WINDOW_MINUTES = 15
# A snapshot stamped further in the future than this is broken, not fresh.
CLOCK_SKEW_TOLERANCE_MINUTES = 5


def ensure_data_dir() -> Path:
    """Create the data dir owner-only (0700): it holds financial data and secrets."""
    DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    with contextlib.suppress(OSError):
        DATA_DIR.chmod(0o700)
    return DATA_DIR


def write_private(path: Path, data: bytes) -> None:
    """Write `data` to `path` created 0600 from the start (no window where it is
    world-readable, unlike write-then-chmod)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    with contextlib.suppress(OSError):
        path.chmod(0o600)  # also tightens a file that already existed with looser bits
