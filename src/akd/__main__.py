"""`python -m akd portfolio` / `akd-app portfolio`: one-shot headless portfolio fetch.

Credentials come from AKD_USER_ID and AKD_PASSWORD in the environment. One JSON document on stdout.
"""
from __future__ import annotations

import os
import sys

from .portfolio import run_portfolio
from .tickets import run_ticket

USAGE = (
    "usage: python -m akd portfolio    (needs AKD_USER_ID and AKD_PASSWORD in the environment)\n"
    "       python -m akd ticket buy  --symbol X --qty N --price P --free-cash C [--account ID] [--ref R]\n"
    "       python -m akd ticket sell --symbol X --qty N --price P --held H      [--account ID] [--ref R]\n"
    "       (ticket checks a trade idea offline: no login, nothing is placed)"
)


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "portfolio":
        sys.exit(run_portfolio(os.environ, sys.stdout, sys.stderr))
    if len(sys.argv) > 1 and sys.argv[1] == "ticket":
        sys.exit(run_ticket(sys.argv[2:], os.environ, sys.stdout))
    print(USAGE, file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
