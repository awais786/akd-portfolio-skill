"""The installer that puts the ticket-approval hook into Claude Code's settings.json, safely."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[1] / "skills" / "akd-portfolio" / "hooks"
spec = importlib.util.spec_from_file_location("install_hook", HOOKS / "install_hook.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)

GUARD = "/abs/path/akd-app/skills/akd-portfolio/hooks/ticket_guard.py"


def _ours(settings):
    return [h for e in settings.get("hooks", {}).get("PreToolUse", []) for h in e["hooks"] if "ticket_guard.py" in h["command"]]


def test_installs_a_bash_pretooluse_hook_that_cannot_brick_other_commands():
    out = installer.merge_hook({}, GUARD)
    (hook,) = _ours(out)
    assert out["hooks"]["PreToolUse"][0]["matcher"] == "Bash|Write|Edit|MultiEdit|NotebookEdit"
    assert hook["type"] == "command" and hook["timeout"] == 10
    # a missing script must be a quiet no-op (python itself would exit 2 = block every Bash call)
    assert hook["command"].startswith(f'[ -f "{GUARD}" ] || exit 0;') and f'python3 "{GUARD}"' in hook["command"]


def test_keeps_every_other_setting_and_hook():
    existing = {"model": "sonnet", "permissions": {"allow": ["Bash(ls)"]},
                "hooks": {"PreToolUse": [{"matcher": "Write", "hooks": [{"type": "command", "command": "other.sh"}]},
                                         {"matcher": "Bash", "hooks": [{"type": "command", "command": "log.sh"}]}],
                          "Stop": [{"hooks": [{"type": "command", "command": "stop.sh"}]}]}}
    out = installer.merge_hook(existing, GUARD)
    assert out["model"] == "sonnet" and out["permissions"] == existing["permissions"] and out["hooks"]["Stop"] == existing["hooks"]["Stop"]
    commands = [h["command"] for e in out["hooks"]["PreToolUse"] for h in e["hooks"]]
    assert "other.sh" in commands and "log.sh" in commands and len(_ours(out)) == 1
    assert existing["hooks"]["PreToolUse"][1]["hooks"] == [{"type": "command", "command": "log.sh"}]  # input not mutated


def test_installing_twice_gives_one_entry_and_updates_a_moved_path():
    once = installer.merge_hook({}, GUARD)
    assert installer.merge_hook(once, GUARD) == once
    moved = installer.merge_hook(once, "/new/place/hooks/ticket_guard.py")
    (hook,) = _ours(moved)
    assert "/new/place/" in hook["command"] and GUARD not in hook["command"]


def test_uninstall_removes_only_ours_and_tidies_up():
    existing = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "log.sh"}]}]}}
    both = installer.merge_hook(existing, GUARD)
    assert installer.remove_hook(both) == existing
    assert installer.remove_hook(installer.merge_hook({}, GUARD)) == {}


def _settings(tmp_path, data=None):
    p = tmp_path / "settings.json"
    if data is not None:
        p.write_text(json.dumps(data))
    return p


def test_cli_installs_backs_up_and_reports(tmp_path, capsys):
    p = _settings(tmp_path, {"model": "sonnet"})
    assert installer.main(["--settings", str(p)]) == 0
    assert _ours(json.loads(p.read_text())) and json.loads(p.read_text())["model"] == "sonnet"
    assert json.loads((tmp_path / "settings.json.akd-backup").read_text()) == {"model": "sonnet"}
    assert "/hooks" in capsys.readouterr().out  # tells the person to reload


def test_cli_creates_a_missing_settings_file(tmp_path):
    p = tmp_path / "new" / "settings.json"
    assert installer.main(["--settings", str(p)]) == 0 and _ours(json.loads(p.read_text()))


def test_dry_run_writes_nothing(tmp_path, capsys):
    p = _settings(tmp_path, {"model": "sonnet"})
    assert installer.main(["--settings", str(p), "--dry-run"]) == 0
    assert json.loads(p.read_text()) == {"model": "sonnet"} and not (tmp_path / "settings.json.akd-backup").exists()
    assert "ticket_guard.py" in capsys.readouterr().out


def test_invalid_json_is_refused_and_left_untouched(tmp_path, capsys):
    p = tmp_path / "settings.json"
    p.write_text("{ not json")
    assert installer.main(["--settings", str(p)]) == 1
    assert p.read_text() == "{ not json" and "not valid JSON" in capsys.readouterr().err


def test_cli_uninstall(tmp_path):
    p = _settings(tmp_path, {"model": "sonnet"})
    installer.main(["--settings", str(p)])
    assert installer.main(["--settings", str(p), "--uninstall"]) == 0
    assert json.loads(p.read_text()) == {"model": "sonnet"}


def test_the_hook_path_it_installs_points_at_the_real_guard_script():
    (hook,) = _ours(installer.merge_hook({}, str(HOOKS / "ticket_guard.py")))
    assert (HOOKS / "ticket_guard.py").exists() and str(HOOKS / "ticket_guard.py") in hook["command"]
    assert pytest is not None
