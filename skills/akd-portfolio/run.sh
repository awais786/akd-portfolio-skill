#!/usr/bin/env bash
# Run the AKD portfolio fetch from anywhere. Resolves this script's real location (so it also works
# through a symlink in ~/.claude/skills), moves to the repo root so `.env` is found, and runs the command.
# First run: creates a private .venv next to the repo and installs the dependencies (needs python3.11+
# and network). Everything setup prints goes to stderr, so stdout stays a single JSON document.
set -euo pipefail
here="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/../.." && pwd)"
cd "$root"

if [ ! -x .venv/bin/python ]; then
  py="${PYTHON:-python3}"
  if ! "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    echo "akd-portfolio: Python 3.11+ is required (set PYTHON=python3.12 to choose one)." >&2
    exit 1
  fi
  echo "akd-portfolio: first run, setting up a private .venv (one time)..." >&2
  "$py" -m venv .venv >&2
  .venv/bin/python -m pip install --quiet --disable-pip-version-check -r requirements.txt >&2
  .venv/bin/python -m pip install --quiet --disable-pip-version-check -e . --no-deps >&2
fi

exec .venv/bin/python -m akd portfolio "$@"
