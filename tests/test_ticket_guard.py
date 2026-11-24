"""The PreToolUse hook: a ticket command always needs the person's approval, shown with the order;
anything that looks like order placement is denied; the hook never answers "allow"."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

GUARD = Path(__file__).resolve().parents[1] / "skills" / "akd-portfolio" / "hooks" / "ticket_guard.py"
RUN = "/Users/someone/dev/akd-app/skills/akd-portfolio/run.sh"
FLAGS = "sell --symbol MLCF --qty 500 --price 105.00 --held 1900 --ref 103.93"


def _hook(command, tool="Bash"):
    payload = json.dumps({"tool_name": tool, "tool_input": {"command": command}})
    proc = subprocess.run([sys.executable, str(GUARD)], input=payload, capture_output=True, text=True)
    out = json.loads(proc.stdout) if proc.stdout.strip() else None
    return proc.returncode, out


def _decision(out):
    return out["hookSpecificOutput"]["permissionDecision"] if out else None


def _reason(out):
    return out["hookSpecificOutput"]["permissionDecisionReason"]


def test_a_ticket_command_asks_and_shows_the_order_clearly():
    code, out = _hook(f'"{RUN}" ticket {FLAGS} --account COAF00000')
    assert code == 0 and _decision(out) == "ask"
    reason = _reason(out)
    for needle in ("SELL", "500", "MLCF", "105.00", "52,500.00", "COAF00000"):
        assert needle in reason, needle
    assert "nothing is placed" in reason.lower()
    assert out["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert "MLCF" in out["systemMessage"]  # also shown to the person directly


def test_the_python_module_form_is_covered():
    code, out = _hook(f"cd /x/akd-app && .venv/bin/python -m akd ticket {FLAGS}")
    assert code == 0 and _decision(out) == "ask" and "MLCF" in _reason(out)


def test_a_quoted_path_with_spaces_is_covered():
    code, out = _hook(f'"/Users/some one/My Dev/akd-app/skills/akd-portfolio/run.sh" ticket {FLAGS}')
    assert _decision(out) == "ask" and "MLCF" in _reason(out)


def test_two_tickets_in_one_call_are_both_shown():
    code, out = _hook(f"{RUN} ticket {FLAGS} && {RUN} ticket buy --symbol OGDC --qty 10 --price 315.00 --free-cash 100000")
    assert _decision(out) == "ask"
    assert "MLCF" in _reason(out) and "OGDC" in _reason(out) and "2 tickets" in _reason(out)


def test_a_ticket_with_no_quantity_or_price_is_denied_and_says_why():
    code, out = _hook(f"{RUN} ticket sell --symbol MLCF")  # no quantity or price
    assert code == 0 and _decision(out) == "deny"
    assert "--qty" in _reason(out) and "--price" in _reason(out) and "nothing is placed" in _reason(out).lower()


@pytest.mark.parametrize("command", [
    "ls -la", "git status", f"{RUN}", f"{RUN} portfolio", "echo ticket", "git log --grep ticket",
    "python3 -m pytest tests/test_tickets.py",
])
def test_other_commands_are_left_to_the_normal_permission_flow(command):
    assert _hook(command) == (0, None)


def test_other_tools_are_ignored():
    assert _hook("anything", tool="Read") == (0, None)


@pytest.mark.parametrize("command", [
    f"{RUN} place --side BUY --symbol OGDC --qty 10 --price 315.00",
    ".venv/bin/python -m akd order --side BUY --symbol OGDC",
    "akd-app submit --symbol OGDC",
    f"cd /x && {RUN} execute",
])
def test_anything_that_looks_like_order_placement_is_denied(command):
    code, out = _hook(command)
    assert code == 0 and _decision(out) == "deny"
    assert "place" in _reason(out).lower() and "AKD" in _reason(out)


@pytest.mark.parametrize("command", [
    f"{RUN} ticket {FLAGS}", f"{RUN} place x", f"{RUN} ticket", "ls", f"{RUN} ticket buy", f"{RUN} sell --symbol X",
])
def test_the_hook_never_says_allow(command):
    assert _decision(_hook(command)[1]) in (None, "ask", "deny")


def test_a_malformed_payload_blocks_instead_of_passing():
    proc = subprocess.run([sys.executable, str(GUARD)], input="{not json", capture_output=True, text=True)
    assert proc.returncode == 2 and proc.stderr.strip()


# --- review fixes: wrappers must not hide a command, and a hook bug must not brick every Bash call -----


def test_a_ticket_wrapped_in_bash_c_is_still_caught():
    code, out = _hook(f'bash -c "{RUN} ticket {FLAGS}"')
    assert _decision(out) == "ask" and "MLCF" in _reason(out)


def test_order_placement_wrapped_in_a_shell_is_still_denied():
    for wrapper in ('bash -c "{c}"', "sh -c '{c}'", 'zsh -lc "{c}"', 'env X=1 bash -c "{c}"'):
        code, out = _hook(wrapper.format(c=f"{RUN} place --side BUY --symbol OGDC"))
        assert _decision(out) == "deny", wrapper


def _guard_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("ticket_guard_under_test", GUARD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_main(mod, command, monkeypatch, capsys):
    import io
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}}).encode()
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(payload), encoding="utf-8"))
    code = mod.main()
    out = capsys.readouterr().out
    return code, (json.loads(out) if out.strip() else None)


def test_an_internal_error_never_blocks_unrelated_commands(monkeypatch, capsys):
    mod = _guard_module()
    monkeypatch.setattr(mod, "_segments", lambda c: (_ for _ in ()).throw(RuntimeError("boom")))
    assert _run_main(mod, "ls -la", monkeypatch, capsys) == (0, None)


def test_an_internal_error_on_a_ticket_like_command_asks_instead_of_passing(monkeypatch, capsys):
    mod = _guard_module()
    monkeypatch.setattr(mod, "_segments", lambda c: (_ for _ in ()).throw(RuntimeError("boom")))
    code, out = _run_main(mod, f"{RUN} ticket {FLAGS}", monkeypatch, capsys)
    assert code == 0 and _decision(out) == "ask"
    assert "internal error" in _reason(out) and "check it yourself" in _reason(out)


def test_buy_and_sell_tickets_both_show_the_right_side():
    _, buy = _hook(f"{RUN} ticket buy --symbol OGDC --qty 10 --price 315.00 --free-cash 100000")
    _, sell = _hook(f"{RUN} ticket {FLAGS}")
    assert _decision(buy) == "ask" and "BUY 10 OGDC @ Rs 315.00" in _reason(buy)
    assert _decision(sell) == "ask" and "SELL 500 MLCF @ Rs 105.00" in _reason(sell)


@pytest.mark.parametrize("command", [
    f"{RUN} sell --symbol OGDC --qty 10 --price 315.00",   # a bare "sell"/"buy" command would look like placing a trade
    f"{RUN} buy --symbol OGDC --qty 10 --price 315.00",
])
def test_a_bare_buy_or_sell_command_is_denied_but_ticket_buy_and_sell_are_not(command):
    _, out = _hook(command)
    assert _decision(out) == "deny"


def test_the_old_side_flag_form_is_denied_because_it_cannot_be_read():
    _, out = _hook(f"{RUN} ticket --side SELL --symbol MLCF --qty 5 --price 100")
    assert _decision(out) == "deny" and "cannot read" in _reason(out).lower()


# ---- limits: the hook denies a ticket that breaks them, whatever the ticket command itself would say ----
def _hook_env(command, **env):
    import os
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    clean = {k: v for k, v in os.environ.items() if not k.startswith("AKD_")}
    env = {"AKD_LIMITS_FILE": "", **env}            # no limits file unless a test asks for one
    proc = subprocess.run([sys.executable, str(GUARD)], input=payload, capture_output=True, text=True, env={**clean, **env})
    return proc.returncode, (json.loads(proc.stdout) if proc.stdout.strip() else None)


@pytest.mark.parametrize("flags, needle", [
    ("buy --symbol MLCF --qty 1000 --price 105.00 --free-cash 9e9 --ref 105", "over the limit of PKR 100,000"),
    ("buy --symbol KTML --qty 10001 --price 1.00 --free-cash 9e9 --ref 1", "over the limit of 10,000 shares"),
    ("buy --symbol MLCF --qty 100 --price 125.00 --free-cash 9e9 --ref 105", "from the reference Rs 105"),
    ("buy --symbol MLCF --qty 100 --price 80.00 --free-cash 9e9 --ref 105", "looks like a typo"),
    ("buy --symbol MLCF --qty 0 --price 105.00 --free-cash 9e9", "above zero"),
    ("buy --symbol MLCF --qty 5 --price 0 --free-cash 9e9", "above zero"),
    ("buy --symbol MLCF --qty 5 --price 105.00 --free-cash 9e9 --ref abc", "not a positive number"),
])
def test_a_ticket_over_the_limits_is_denied_with_the_reason(flags, needle):
    code, out = _hook_env(f"{RUN} ticket {flags}")
    assert code == 0 and _decision(out) == "deny", out
    assert needle in _reason(out)


def test_one_bad_ticket_denies_the_whole_call_and_names_it():
    good = "buy --symbol MLCF --qty 100 --price 105.00 --free-cash 1e6 --ref 105"
    bad = "buy --symbol KTML --qty 5000 --price 40.00 --free-cash 1e6 --ref 40"        # PKR 200,000
    code, out = _hook_env(f"{RUN} ticket {good} && {RUN} ticket {bad}")
    assert _decision(out) == "deny" and "KTML" in _reason(out) and "2. " in _reason(out) and "MLCF" not in _reason(out)


def test_a_ticket_inside_every_limit_still_just_asks():
    code, out = _hook_env(f"{RUN} ticket buy --symbol MLCF --qty 900 --price 105.40 --free-cash 1e6 --ref 105.80")
    assert _decision(out) == "ask" and "MLCF" in _reason(out)


def test_no_reference_price_is_allowed_but_the_prompt_says_the_price_was_not_checked():
    code, out = _hook_env(f"{RUN} ticket buy --symbol MLCF --qty 100 --price 105.00 --free-cash 1e6")
    assert _decision(out) == "ask" and "could not be checked" in _reason(out)


def test_limits_can_be_changed_on_purpose_in_the_environment():
    flags = "buy --symbol MLCF --qty 1000 --price 105.00 --free-cash 9e9 --ref 105"
    assert _decision(_hook_env(f"{RUN} ticket {flags}")[1]) == "deny"
    assert _decision(_hook_env(f"{RUN} ticket {flags}", AKD_TICKET_MAX_VALUE="200000")[1]) == "ask"


# ---- every limit is a variable: environment > limits file > default ----
def _limits_file(tmp_path, text):
    f = tmp_path / "limits.env"
    f.write_text(text)
    return str(f)


BUY_1000 = "buy --symbol MLCF --qty 1000 --price 105.00 --free-cash 9e9 --ref 105"   # PKR 105,000


def test_the_limits_file_is_read_by_the_hook(tmp_path):
    f = _limits_file(tmp_path, "AKD_TICKET_MAX_VALUE=200000\n")
    assert _decision(_hook_env(f"{RUN} ticket {BUY_1000}", AKD_LIMITS_FILE=f)[1]) == "ask"
    f = _limits_file(tmp_path, "# comment\nAKD_TICKET_MAX_VALUE = 50000\n")
    assert _decision(_hook_env(f"{RUN} ticket {BUY_1000}", AKD_LIMITS_FILE=f)[1]) == "deny"


def test_the_environment_wins_over_the_limits_file(tmp_path):
    f = _limits_file(tmp_path, "AKD_TICKET_MAX_VALUE=200000\n")
    code, out = _hook_env(f"{RUN} ticket {BUY_1000}", AKD_LIMITS_FILE=f, AKD_TICKET_MAX_VALUE="50000")
    assert _decision(out) == "deny"


def test_the_hook_reads_only_ticket_limits_from_the_file_never_other_lines(tmp_path):
    f = _limits_file(tmp_path, "AKD_PASSWORD=hunter2\nAKD_TICKET_MAX_QTY=5\nSOMETHING=else\n")
    code, out = _hook_env(f"{RUN} ticket buy --symbol MLCF --qty 6 --price 1.00 --free-cash 9e9 --ref 1", AKD_LIMITS_FILE=f)
    assert _decision(out) == "deny" and "5 shares" in _reason(out)
    assert "hunter2" not in json.dumps(out)


@pytest.mark.parametrize("bad", ["inf", "Infinity", "nan", "-1", "-5", "0", "abc", ""])
def test_an_infinite_or_broken_limit_never_means_no_limit(tmp_path, bad):
    f = _limits_file(tmp_path, f"AKD_TICKET_MAX_VALUE={bad}\n")
    assert _decision(_hook_env(f"{RUN} ticket {BUY_1000}", AKD_LIMITS_FILE=f)[1]) == "deny"
    assert _decision(_hook_env(f"{RUN} ticket {BUY_1000}", AKD_TICKET_MAX_VALUE=bad)[1]) == "deny"


def test_a_missing_limits_file_means_the_defaults(tmp_path):
    missing = str(tmp_path / "nope.env")
    assert _decision(_hook_env(f"{RUN} ticket {BUY_1000}", AKD_LIMITS_FILE=missing)[1]) == "deny"


def test_the_defaults_in_the_hook_the_ticket_module_and_the_example_file_agree():
    import importlib.util

    from akd import tickets
    spec = importlib.util.spec_from_file_location("ticket_guard_under_test", GUARD)
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    example = tickets.read_limits_file(GUARD.parents[3] / "limits.env.example")
    assert example == tickets.DEFAULTS
    assert guard.LIMIT_DEFAULTS == {k: v for k, v in tickets.DEFAULTS.items() if k in guard.LIMIT_DEFAULTS}


# ---- the bypasses a reviewer found: every way of writing a ticket is read the way the command reads it ----
OVER = "--symbol MLCF --price 1000 --free-cash 9e9 --ref 1000"          # with --qty 99999999: far over every limit


@pytest.mark.parametrize("form", [
    "--qty=99999999",                      # = form: argparse reads it, the old parser did not
    "--qty 99999999",
])
def test_the_equals_form_is_read_and_denied_like_the_spaced_form(form):
    _, out = _hook_env(f"{RUN} ticket buy {OVER} {form}")
    assert _decision(out) == "deny" and "over the limit" in _reason(out)


@pytest.mark.parametrize("flags, why", [
    ("--qt 10 --price 100 --symbol MLCF", "cannot read"),                 # abbreviation is not accepted
    ("--qty 1e6 --price 100 --symbol MLCF", "over the limit"),            # read as 1,000,000 exactly as the command reads it
    ("--qty 10 --price 100 --symbol MLCF --ref=", "not a positive number"),   # an empty --ref is not "no reference"
    ("--qty 10 --price inf --symbol MLCF", "above zero"),
    ("--qty nan --price 100 --symbol MLCF", "whole number"),
    ("--qty 10 --price 100 --symbol MLCF --ref -3", "not a positive number"),
    ("--qty 10 --price 100 --symbol MLCF extra", "cannot read"),          # a stray word
    ("--qty 10 --price 100 --symbol MLCF --bogus 1", "cannot read"),
])
def test_an_unreadable_or_odd_ticket_is_denied_never_just_asked(flags, why):
    _, out = _hook_env(f"{RUN} ticket buy {flags} --free-cash 9e9")
    assert _decision(out) == "deny" and why in _reason(out)


@pytest.mark.parametrize("command", [
    "cd /Users/x/dev/akd-app/skills/akd-portfolio && ./run.sh ticket buy --symbol MLCF --qty 900 --price 105.40 --free-cash 9e9 --ref 105.8",
    "cd /x && ./run.sh ticket buy --symbol MLCF --qty 900 --price 105.40 --free-cash 9e9 --ref 105.8",
    'eval "./run.sh ticket buy --symbol MLCF --qty 900 --price 105.40 --free-cash 9e9 --ref 105.8"',
    "bash -c './run.sh ticket buy --symbol MLCF --qty 900 --price 105.40 --free-cash 9e9 --ref 105.8' arg0",
    "bash -c './run.sh ticket buy --symbol MLCF --qty 900 --price 105.40 --free-cash 9e9 --ref 105.8' a b c",
    "env X=1 ./run.sh ticket buy --symbol MLCF --qty 900 --price 105.40 --free-cash 9e9 --ref 105.8",
])
def test_every_way_of_running_the_launcher_still_gets_the_approval_prompt(command):
    _, out = _hook_env(command)
    assert _decision(out) == "ask" and "MLCF" in _reason(out), command


@pytest.mark.parametrize("command", [
    'eval "./run.sh ticket buy --symbol MLCF --qty 99999999 --price 105.40 --free-cash 9e9 --ref 105.8"',
    "bash -c './run.sh ticket buy --symbol MLCF --qty=99999999 --price 105.40 --free-cash 9e9' arg0",
    "cd /x && ./run.sh ticket buy --symbol MLCF --qty 5000000 --price 105.40 --free-cash 9e9",
])
def test_the_same_forms_are_denied_when_over_a_limit(command):
    _, out = _hook_env(command)
    assert _decision(out) == "deny", command


def test_a_script_piped_into_a_shell_asks_because_it_cannot_be_read():
    _, out = _hook_env('echo "./run.sh ticket buy --symbol MLCF --qty 5 --price 100" | sh')
    assert _decision(out) == "ask" and "could not read" in _reason(out).lower()


def test_ordinary_commands_that_only_mention_tickets_are_not_prompted():
    for command in ('git commit -m "fix run.sh ticket check"', "grep -rn ticket skills/akd-portfolio/run.sh", "cat limits.env",
                    "grep AKD_TICKET limits.env", "diff limits.env limits.env.example", "cp limits.env limits.env.bak"[:0] or "ls limits.env"):
        assert _hook_env(command) == (0, None), command


# ---- the payload ----
def _raw_hook(data: bytes):
    proc = subprocess.run([sys.executable, str(GUARD)], input=data, capture_output=True)
    return proc.returncode, proc.stdout.decode(), proc.stderr.decode()


@pytest.mark.parametrize("data", [b"[1, 2]", b'"text"', b"null", b"42", b"\xff\xfe\x00bad", b"{not json", b""])
def test_a_payload_that_is_not_a_json_object_blocks_with_exit_2_not_a_traceback(data):
    code, _out, err = _raw_hook(data)
    assert code == 2 and "Traceback" not in err and "blocking to be safe" in err


def test_a_tool_input_that_is_not_an_object_is_left_alone():
    code, out, _ = _raw_hook(json.dumps({"tool_name": "Bash", "tool_input": "oops"}).encode())
    assert code == 0 and out == ""


# ---- the limits file cannot be loosened without you seeing it ----
@pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit"])
def test_editing_the_limits_file_with_an_edit_tool_asks_first(tool):
    payload = json.dumps({"tool_name": tool, "tool_input": {"file_path": "/Users/x/dev/akd-app/limits.env"}})
    proc = subprocess.run([sys.executable, str(GUARD)], input=payload, capture_output=True, text=True)
    out = json.loads(proc.stdout)
    assert proc.returncode == 0 and _decision(out) == "ask" and "order limits" in _reason(out)


def test_editing_other_files_with_an_edit_tool_is_left_alone():
    for path in ("/Users/x/dev/akd-app/limits.env.example", "/Users/x/dev/akd-app/README.md"):
        payload = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": path}})
        proc = subprocess.run([sys.executable, str(GUARD)], input=payload, capture_output=True, text=True)
        assert proc.returncode == 0 and proc.stdout == "", path


@pytest.mark.parametrize("command", [
    "sed -i '' 's/100000/9999999/' limits.env",
    "echo AKD_TICKET_MAX_VALUE=9999999 >> limits.env",
    "cat > limits.env",
    "cp limits.env.example limits.env",
    "printf x | tee limits.env",
    "python3 -c \"open('limits.env','w')\"",
])
def test_a_bash_command_that_writes_the_limits_file_asks_first(command):
    _, out = _hook_env(command)
    assert _decision(out) == "ask" and "order limits" in _reason(out), command


# ---- one reader of the limits file in the library, one in the standalone hook: they must agree ----
def test_the_hook_and_the_ticket_module_read_a_limits_file_identically(tmp_path, monkeypatch):
    from akd import tickets
    f = tmp_path / "limits.env"
    f.write_text("# c\n\nAKD_TICKET_MAX_VALUE = 123\nAKD_TICKET_MAX_QTY='45'\nAKD_TICKET_PRICE_BAND_PCT=\"6\"\n"
                 "AKD_PASSWORD=x\nNOT_A_LIMIT=1\nAKD_TICKET_FEE_BUFFER_PCT\n  AKD_TICKET_MAX_QTY = 77 \n")
    monkeypatch.setenv("AKD_LIMITS_FILE", str(f))
    hook = {k: v for k, v in _guard_module()._limits_file_values().items()}
    assert hook == tickets.read_limits_file(f) and "AKD_PASSWORD" not in hook and hook["AKD_TICKET_MAX_QTY"] == "77"


# ---- a word after the launcher that the tool does not have is asked about, not waved through ----
AKD_RUN = "/Users/someone/dev/akd-app/skills/akd-portfolio/run.sh"


@pytest.mark.parametrize("command", [f"{AKD_RUN} frobnicate", f"{AKD_RUN} --help", "python -m akd confirm --id 1",
                                     f"{AKD_RUN} ticket buy --symbol MLCF --qty 5 --price 100 --free-cash 9e9 --ref 100 && {AKD_RUN} frobnicate"])
def test_an_unknown_akd_subcommand_asks(command):
    _, out = _hook_env(command)
    assert _decision(out) == "ask" and "does not know" in _reason(out), command


def test_the_known_subcommands_and_other_projects_launchers_are_left_alone():
    for command in (AKD_RUN, f"{AKD_RUN} portfolio", "./run.sh start", "/other/project/run.sh --port 80"):
        assert _hook_env(command) == (0, None), command
