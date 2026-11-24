#!/usr/bin/env python3
"""Install (or remove) the AKD ticket-approval hook in Claude Code's user settings.

    python3 skills/akd-portfolio/hooks/install_hook.py            # install
    python3 skills/akd-portfolio/hooks/install_hook.py --dry-run  # show what it would write
    python3 skills/akd-portfolio/hooks/install_hook.py --uninstall

It only adds or removes its own PreToolUse entry (found by `ticket_guard.py` in the command); every other
setting and hook is kept. The first change makes a one-time backup next to the file (settings.json.akd-backup).
Run `/hooks` in Claude Code afterwards (or start a new session) so it reloads. Standard library only.
"""
from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
from pathlib import Path

GUARD = Path(__file__).resolve().parent / "ticket_guard.py"
MARKER = "ticket_guard.py"
# Bash for tickets; the edit tools so a change to limits.env (your order limits) is shown to you first.
MATCHER = "Bash|Write|Edit|MultiEdit|NotebookEdit"


def hook_command(guard: str) -> str:
    # If the script is ever gone, do nothing: python itself would exit 2, which BLOCKS every Bash call.
    return f'[ -f "{guard}" ] || exit 0; exec python3 "{guard}"'


def _is_ours(hook: dict) -> bool:
    return MARKER in str(hook.get("command", ""))


def remove_hook(settings: dict) -> dict:
    out = copy.deepcopy(settings)
    pre = out.get("hooks", {}).get("PreToolUse", [])
    kept = []
    for entry in pre:
        hooks = [h for h in entry.get("hooks", []) if not _is_ours(h)]
        if hooks:
            kept.append({**entry, "hooks": hooks})
    if kept:
        out["hooks"]["PreToolUse"] = kept
    elif "hooks" in out:
        out["hooks"].pop("PreToolUse", None)
        if not out["hooks"]:
            del out["hooks"]
    return out


def merge_hook(settings: dict, guard: str) -> dict:
    out = remove_hook(settings)  # idempotent: replaces an older copy (e.g. one at a moved path)
    entry = {"matcher": MATCHER, "hooks": [{
        "type": "command", "command": hook_command(guard), "timeout": 10, "onFailure": "block",
        "statusMessage": "Checking AKD tickets and limits...",
    }]}
    out.setdefault("hooks", {}).setdefault("PreToolUse", []).append(entry)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install the AKD ticket-approval hook.")
    parser.add_argument("--settings", default="~/.claude/settings.json", help="settings file to edit")
    parser.add_argument("--uninstall", action="store_true", help="remove the hook")
    parser.add_argument("--dry-run", action="store_true", help="print the result, write nothing")
    args = parser.parse_args(argv)
    path = Path(args.settings).expanduser()

    settings: dict = {}
    if path.exists():
        try:
            settings = json.loads(path.read_text() or "{}")
        except json.JSONDecodeError as exc:
            print(f"{path} is not valid JSON ({exc}); not touching it. Fix it first.", file=sys.stderr)
            return 1
    updated = remove_hook(settings) if args.uninstall else merge_hook(settings, str(GUARD))
    text = json.dumps(updated, indent=2) + "\n"
    if args.dry_run:
        print(text, end="")
        return 0
    if path.exists():
        backup = path.with_name(path.name + ".akd-backup")
        if not backup.exists():
            shutil.copy2(path, backup)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    action = "removed from" if args.uninstall else "installed in"
    print(f"AKD ticket-approval hook {action} {path}.")
    print("Open /hooks in Claude Code (or start a new session) so it takes effect.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
