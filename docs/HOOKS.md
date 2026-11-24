# Hooks: where they are and what values they use

One Claude Code hook protects the ticket workflow. It never places anything; it only reads the command text.

## Files

| File | Role |
|---|---|
| `skills/akd-portfolio/hooks/ticket_guard.py` | The hook. Reads the tool call as JSON on stdin and answers `deny`, `ask` or nothing. |
| `skills/akd-portfolio/hooks/install_hook.py` | Registers the hook in `~/.claude/settings.json` (re-run it after an update so the matcher below is current). `--dry-run` previews, `--uninstall` removes it. A backup is made first. |
| `limits.env` | Your limit values (git-ignored). Template: `limits.env.example`. |
| `tests/test_ticket_guard.py`, `tests/test_install_hook.py` | The hook's tests (`make test`). |
| `docs/CONFIGURATION.md` | The same variables, documented. |

## Where it is registered

`~/.claude/settings.json` (global, not per project):

```
hooks.PreToolUse[]
  matcher:   Bash|Write|Edit|MultiEdit|NotebookEdit
  command:   [ -f ".../ticket_guard.py" ] || exit 0; exec python3 ".../ticket_guard.py"
  timeout:   10
  onFailure: block
  statusMessage: "Checking AKD tickets and limits..."
```

If the script file is missing, the command exits 0 (the hook does nothing) instead of blocking every Bash call.
After changing the registration, open `/hooks` in Claude Code or start a new session.

## What it decides

| Bash command | Answer |
|---|---|
| `akd ticket buy|sell ...` inside every limit | `ask`: shows side, symbol, quantity, limit price, total, account. It never answers `allow`, so no permission rule can skip the prompt. |
| a ticket that breaks a limit | `deny`, with the reasons. One bad ticket in a multi-ticket command denies the whole call. |
| a ticket the hook cannot read (unknown flag, abbreviation like `--qt`, stray word, missing value) | `deny`. Flags are read exactly the way `akd ticket` reads them (`--qty=5` works, abbreviations do not). |
| a ticket with no `--ref` | `ask`, and the prompt says the price could not be checked. |
| any way of running `run.sh ticket ...` (after a `cd`, `eval "..."`, `bash -c '...' arg0`, `env X=1 ...`) | handled as above. A script piped into a shell cannot be read, so it `ask`s. |
| a Write/Edit of `limits.env`, or a Bash command that writes it (`sed -i`, `>>`, `tee`, `cp`...) | `ask` ("this changes your order limits"). Reading it (`cat`, `grep`, `ls`, `diff`) is not prompted. |
| `akd place|order|submit|execute|buy|sell|send|trade|cancel|amend|modify` | `deny` (order placement does not exist in this project). |
| any other word after the AKD launcher (the tool only has `portfolio` and `ticket`) | `ask` ("a command the guard does not know"). Another project's `run.sh` is left alone. |
| anything else | no output (normal permission flow). |
| a payload that is not valid UTF-8 JSON, or not a JSON object | exit 2, which blocks the call (fail closed). |
| an internal error | does not block Bash; asks if the command looks like an AKD ticket. |

## Limit values

Four variables (`AKD_TICKET_MAX_VALUE`, `AKD_TICKET_MAX_QTY`, `AKD_TICKET_PRICE_BAND_PCT`, `AKD_TICKET_FEE_BUFFER_PCT`), read from the environment, then `limits.env`, then the defaults. The table, precedence and fallback rules are in [CONFIGURATION.md](CONFIGURATION.md); the hook uses all but the fee buffer.

- The hook reads only the `AKD_TICKET_*` lines of `limits.env` and never opens `.env` (it holds the AKD password).
- The defaults are written in three places that tests keep in step: `src/akd/tickets.py` (`DEFAULTS`), the hook (`LIMIT_DEFAULTS`, because it cannot import the `akd` package) and `limits.env.example`. The two limits-file parsers are tested against the same text.

## Limits of this protection

- It only inspects Bash commands and the edit tools run by Claude. It cannot stop an order placed by hand in the AKD app.
- A command it cannot parse at all (for example one built from command substitution) is only caught when it also mentions `run.sh`, `akd-app` or `-m akd` together with `ticket` and runs through a shell, `xargs` or `source`.
- It checks the command text, not what AKD would do. The ticket command still checks cash and shares held.
- If order sending is ever built (see `docs/ORDER-ENTRY.md`), it must apply the same limits as a second gate.
