#!/usr/bin/env bash
# Run the AKD portfolio fetch from anywhere. Resolves this script's real location (so it also works
# through a symlink in ~/.claude/skills), moves to the repo root so `.env` is found, and runs the command.
set -euo pipefail
here="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/../.." && pwd)"
if [ ! -x "$root/.venv/bin/python" ]; then
  echo "akd-portfolio: $root/.venv is missing. Run 'make install' in $root first." >&2
  exit 1
fi
cd "$root"
exec "$root/.venv/bin/python" -m akd portfolio "$@"
