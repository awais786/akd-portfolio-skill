# Portfolio advice skill: design

Status: **scope reduced 2026-11-15.** The owner agreed the engine and watcher add little over looking at the data. v0 is no-code: the review rules live as instructions in the `akd-portfolio` skill (see its "Review flags" section). The rules engine (`akd advise`), averaging/switching, and the Part 2 watcher are **postponed** until v0 has been used for a couple of weeks and proves worth building. The rest of this document is the postponed design.

Builds on the existing `akd-portfolio` fetch.

## Intent and why

The owner holds PSX stocks in several AKD accounts and wants to be told **what to do**: when to sell or trim, when to average down, and when to switch from one stock to another. Historical charts and price data are not the point (many sites provide them). The value is judging the owner's *own* positions: cost basis, how much of each account one stock takes up, the same stock held in several accounts, and idle cash. Rules run in code so they are consistent and testable; Claude presents the result as short, prioritised actions per account.

## Goals

- From one portfolio fetch, produce for each account an ordered list of actions: `TRIM`, `SELL_REVIEW`, `AVERAGE_DOWN`, `SWITCH` (sell X, buy Y), or `HOLD`, each with a one-line reason and the numbers behind it.
- Show cross-account overlap (one stock in several accounts) as a combined exposure.
- Thresholds editable by the owner in one config file, with sensible defaults.
- Use only light, current market context per stock (latest price, 52-week range position, P/E, free float, 1-year and year-to-date change) from PSX's public company pages. No history.
- Final output to the owner is only the actions (plus a short overlap note), not a report.

## Non-goals

- Placing, previewing or staging orders. Ever. The owner places any trade in AKD's own app.
- Historical charts or price-history storage.
- Screening the whole market for new stocks. Switch candidates come only from the owner's own universe (stocks they already hold in any account, plus an optional watchlist in the config).
- Tax or transaction-cost modelling (Pakistani capital gains tax, brokerage). Realising a gain or loss has costs; the output says so, it does not compute them.
- Price prediction, intraday signals, "live" data.
- Moving money between accounts. Accounts have separate cash; a switch is always within one account.

## Anti-goals (a failure even if it technically works)

- Recommending averaging down into an already concentrated or collapsing position.
- Showing failed or missing market data as if it were known, or defaulting it to a number. Unknown stays unknown and the affected rule says so.
- Hiding uncertainty. "No action" is a valid and common answer; the engine must not invent actions.
- Sending anything beyond a ticker symbol to an outside service (no quantities, values, costs or account IDs).
- A second AKD login: the advice step must reuse the data from the single fetch.

## Constraints

- Inherits the repo rules (`CLAUDE.md`): read-only, one login attempt per account per run, passwords never written or printed, absence is never emptiness (`null` is unknown, never 0), `protocol/` untouched.
- PSX data comes from plain public HTML pages (`https://dps.psx.com.pk/company/<SYMBOL>`), one request at a time with a short pause, an honest User-Agent, an in-memory cache for the length of the run (nothing written to disk). PSX blocks its live JSON feeds for programs; the code must not try to get around any block. On a non-200 response the symbol's context is "unavailable" and the run continues.
- Python 3.11+, existing dependencies only (`httpx`); config as TOML (`tomllib` is in the standard library). No new dependencies.
- Tests are offline: PSX responses are redacted fixtures; no network.

## Approaches considered

| | What | Trade-off |
|---|---|---|
| A. Claude only, no code | The skill tells Claude to research and judge from the JSON | Fastest, but not repeatable or testable; thresholds drift between runs |
| B. Rules engine in code + Claude presents (**chosen**) | Python computes the actions from the fetch + light PSX context; Claude writes them up | Consistent, testable, editable thresholds; more to build |
| C. Adopt `daily_stock_analysis` | Finish its unbuilt PSX support, run its dashboard | Heavy; needs an outside AI key; knows nothing about the owner's accounts or costs |

The owner chose the direction of B in conversation ("it makes sense", rules plus judgment, no history).

## Design

### Pieces

1. `src/akd/advice.py`: pure function `advise(portfolio: dict, context: dict[str, dict], rules: dict) -> dict`. No I/O, no clock. Input is the `{"accounts": [...]}` document from `akd portfolio`, per-symbol market context, and the rules. Output is `{"accounts": [{"account", "actions": [...], "notes": [...]}], "overlap": [...]}`.
2. `src/akd/psx_context.py`: `fetch_context(symbols: list[str], *, http=None) -> dict[str, dict | None]` reading the public company pages (`price`, `week52_low`, `week52_high`, `pe`, `free_float_pct`, `change_1y_pct`, `change_ytd_pct`, `fetched_at`); `None` for a symbol that could not be read.
3. `config/advice.toml`: default thresholds, committed; plus optional `watchlist = [...]` of extra switch candidates.
4. Subcommand `python -m akd advise`: reads the portfolio JSON on **stdin** (so it is piped from `portfolio` and no file with positions is written), fetches context for the distinct symbols, prints one JSON document. The skill pipes: `run.sh | advise`.
5. `skills/akd-portfolio-advice/SKILL.md`: how to run the pipe once, how to read the result, and how to present only the actions.

### Rules (default thresholds, all editable in `config/advice.toml`)

Per account, per position (weight = position market value / account total market value):

- **Concentration:** weight > 25% -> `TRIM` (reason: concentration); > 40% -> `TRIM` flagged strong.
- **Deep loser:** loss from cost > 15% -> `SELL_REVIEW`, ranked higher if the price is in the lower half of its 52-week range (cut, hold, or average down is the owner's call; the reason says which signals fired).
- **Big winner:** gain from cost > 20% -> `TRIM` (partial profit).
- **Average down:** allowed only if loss from cost is between 10% and 30%, price is in the lower half of its 52-week range, P/E is known and below the configured ceiling, **and** the post-add weight stays <= 25% **and** the account's free cash covers it. Output includes the maximum quantity that keeps the weight under the cap. Never emitted for a position already over the concentration limit.
- **Switch:** when an account has a `TRIM` or `SELL_REVIEW` position and an under-weight (< 10%) candidate in the owner's universe that is not itself a deep loser and has known context, emit `SWITCH from -> to` with an indicative amount (the trim proceeds, capped by the candidate's room under the 25% cap).
- **Idle cash:** free cash above 10% of account value -> note.
- **Overlap:** a stock held in more than one account -> one `overlap` entry with combined quantity, value, and the accounts involved.
- **Otherwise** `HOLD` (listed only as a count, not line by line).

Actions are ordered by priority: strong concentration, deep loser, concentration, big winner, average down, switch.

### Output to the owner

Per account: its actions in priority order, each one line (symbol, action, why, key numbers). Then one overlap note. "No action" for accounts with nothing triggered. The skill always ends with "Research, not licensed advice. Nothing was placed. Costs and capital gains tax are not included."

## Decided by the owner

- Algorithm-based suggestions on their own portfolio; actions to cover selling, averaging and switching between stocks.
- No interest in historical data or charts.
- Use open, public sources; do not use another person's platform tools.

## My calls (not yet confirmed by the owner)

- The default thresholds above (25% / 40% concentration, 15% / 20% loss and gain, 10-30% average-down window, 10% idle cash, 10% under-weight).
- Switch candidates only from the owner's own holdings plus an optional config watchlist.
- Stdin piping and no file with positions.
- A new skill `akd-portfolio-advice`, separate from the fetch skill, in this repo.
- Config in `config/advice.toml`.

## Left to the builder

- Exact HTML parsing of the PSX company page (`quote__close`, volume, 52-week, P/E blocks), tolerant of layout changes (degrade to unavailable).
- Output JSON field names, priority tie-breaks, rounding.
- Tests: pure-function tests of every rule with table-driven fixtures; a redacted PSX page fixture for the parser; the stdin/stdout contract; a test that nothing but tickers is ever sent out (assert on the request URLs); a test that a 403 or garbage page yields `None` and a stated "unavailable" instead of a default.
- Docs: `README.md`, `CLAUDE.md`, `docs/CONFIGURATION.md`, `docs/STRUCTURE.md`.

---

# Part 2: daily briefing and market-hours watcher (added 2026-11-15)

## Intent

The owner wants a daily rhythm that works **only inside a Claude Code session** (no phone or desktop notifications for now): an early-morning briefing on what to do, then something that keeps an eye on the held stocks until the market closes and speaks up only when needed.

## Approaches considered

| | What | Verdict |
|---|---|---|
| A. Self-paced `/loop` | The model wakes every ~10 minutes | Costs model effort on every quiet tick |
| B. Timed in-session jobs | Cron-style prompts | The tool docs say not for live watching; jobs fire late, only when idle, and expire in 7 days |
| C. Background watcher + Monitor (**chosen**, evaluation delegated to me by the owner) | A tested Python watcher prints alert lines; Claude's Monitor streams them into the session | Model works only on events; rules stay in code; needs more building |

## Design

- **Skill `/akd-day`** (user-triggered; no timer): runs the Part 1 pipeline once (`run.sh | advise`), shows the morning briefing (actions per account plus the trigger price levels), then starts the watcher.
- **Watcher `python -m akd watch`:**
  - Receives the briefing's levels on **stdin** (a pipe from `advise`), never from a file. It runs as a background process so it survives Monitor's 30-minute cap; its stdout lines go to a log that Monitor follows. Claude re-arms the monitor on each expiry.
  - Every cycle (default 10 minutes, never under 30 s, one request per symbol with a pause) reads the public PSX company page of each **held** symbol (tickers only; no AKD login ever happens in the watcher).
  - Alerts (each fires once per level and direction): a price crosses a briefing level; a move of at least 3% against the previous close; a price within 1% of its circuit limit.
  - Output lines are prefixed `ALERT`, `ERROR`, `STALE`, `HEARTBEAT` (hourly) or `RECAP` (at close). The watcher also emits an `ERROR`/`STOPPED` line on its own failure or when PSX stops answering, so silence is never mistaken for "all quiet". It exits after emitting the recap at market close.
  - The page's own timestamp is checked; data older than 15 minutes during market hours is reported as `STALE`.
  - Market open/close are configurable (`market_open = "09:15"`, `market_close = "15:30"`, `Asia/Karachi`), defaults to be confirmed by the owner; none on weekends.
  - Politeness and safety: honest User-Agent, one request at a time, backs off on non-200, and never tries to get around a block.
- **Positions are assumed unchanged intraday.** The watcher does not see trades made in AKD; the owner re-runs `/akd-day` to refresh.

## Non-goals (Part 2)

Phone or desktop notifications; unattended logins or a timed auto-start (possible later as an optional extra); intraday indicators; any order placement.

## Anti-goals (Part 2)

Alerts firing repeatedly for the same level; a silent watcher that died; a second AKD login during the day; positions or levels written to disk.

## Build order

1. Part 1: advice rules engine and the morning briefing (`akd advise`).
2. Part 2: the watcher (`akd watch`) and `/akd-day`.

## My calls (not yet confirmed by the owner)

The 10-minute cycle, 3% move and 1% circuit-limit thresholds, 15-minute staleness limit, the 09:15 to 15:30 default hours, manual start only, and levels passed by pipe.
