"""`python -m akd portfolio` / `akd-app portfolio`: one-shot headless portfolio fetch.

Credentials come from AKD_USER_ID and AKD_PASSWORD in the environment. One JSON document on stdout.
"""
from __future__ import annotations

import os
import sys

from .portfolio import run_portfolio

USAGE = "usage: python -m akd portfolio    (needs AKD_USER_ID and AKD_PASSWORD in the environment)"


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "portfolio":
        sys.exit(run_portfolio(os.environ, sys.stdout, sys.stderr))
    print(USAGE, file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
