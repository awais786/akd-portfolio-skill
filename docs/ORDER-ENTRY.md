# Order entry: staged plan

**Status: NOT implemented.** `CLAUDE.md` rule 1 still applies (no order code, never guess AKD's message, never store a PIN). It changes only at stage 4, when the owner approves it.

Decided by the owner on 2026-11-24: build order placing, in stages, with these gates.

## Gates (every stage, every order)

1. **Confirm every order.** Show side, symbol, quantity, limit price, total and account. Nothing is sent without an explicit yes for that one order.
2. **Limit orders only.** No market orders. Refuse a price outside the PSX band.
3. **Max 100,000 PKR per order** (`AKD_TICKET_MAX_VALUE`). Larger plans are split into several orders, each confirmed.
4. **PIN typed at a terminal prompt each time.** Never in `.env`, chat, logs, exceptions, tests or on disk. The PIN is held in memory only for the one send.
5. **No automation.** Alerts may tell the owner which ticket to enter. They never send an order.
6. One login attempt per account per run, no retry on rejection (rule 4 in `CLAUDE.md`).

## Stages

| # | What | Who | Done when |
|---|---|---|---|
| 1 | Record ONE real 1-share order (and its cancel) in TradeCast with `tcpdump`. See `CAPTURE-GUIDE.md` | Owner | A redacted `make capture-report` output exists |
| 2 | Decode the order message from the **redacted report only** (never read the `.pcap`). Fill in the field layout below | Claude | Fields below filled from evidence, not guessed |
| 3 | Build the message in a pure `protocol/` function and test it by replaying against the capture-derived layout. No network | Claude | Tests pass; builds byte-identical to the captured order |
| 4 | Owner approves lifting `CLAUDE.md` rule 1. Add the gateway with all gates above. First live order: 1 share, limit 3-5% below price so it does not fill, placed by the owner, then cancelled | Both | The order shows in TradeCast as sent by the tool |
| 5 | Normal use, still confirm-every-order | Owner | |

## Order message layout (fill from the redacted report in stage 2)

First real capture (2026-11-24, ONE order: BUY 1 MLCF, filled at once, no cancel, no login in the recording). Observed from the redacted report only. **Tentative: one order shows positions, not which fields vary.** Do not fill in anything else from guesses, from `Beans.BuySellOrder` field names, or from the invented `NEW_ORDER` in `tests/capture_fixtures.py`.

`MSG-CLIENT|NEW_ORDER|<f0>;<f1>;...;<f15>*` (the body is ONE `|` field, split on `;` and `$`):

| Piece | Observed | Confirmed by label? |
|---|---|---|
| 0 | 4 digits, then `$TRD` | No. The 4 digits are shown only as `<digits:4>`; may be the PIN |
| 1 | `NEW` | No |
| 2 | side (`BUY`) | Yes (the report labelled it) |
| 3 | symbol (`MLCF`) | Yes |
| 4 | `REG` (market board) | No |
| 5 | a price shaped `ddd.dd` | Not labelled (exact typed price was not given); likely the limit price |
| 6 | `1` | Labelled `QTY?` (a single digit is ambiguous) |
| 7, 8 | empty | |
| 9, 10 | the account code, twice | Yes: masked in the second capture once the right account was given |
| 11 | a 4-letter code (`VQBM` in capture 1, `RRSH` in captures 2 and 3), repeated in every server reply | Constant across the 3 orders of capture 3, so NOT a per-order id. Probably a session or terminal code; where it comes from (the login?) is unknown |
| 12 | `2` (capture 1), `1` (capture 2), then `4`, `5`, `6` (the three orders of capture 3) | Rises by one per order inside one capture, so likely a per-session order counter. Not confirmed |
| 13 | 23-character text shaped like a date and time | Unknown |
| 14 | a decimal `d.d` | Unknown |
| 15 | empty | |

Replies seen: `ORDER_SENT`, two `ORDER_EXE` (an execution), `ORDER_EXE_LOG`; the client answers each `ORDER_EXE` with `ORDER_EXE_ACK`. No cancel message and no trade-socket login were in capture 1.

Capture 2 (BUY 2 DCR, filled at once, no cancel, no sell): the NEW_ORDER has the same 16 pieces in the same order, so the layout is stable for BUY. `ORDER_EXE_LOG` carries the fill details (`FIL$...;symbol;market;...;side;qty;price;...`). The web-service side showed `SecureChangePaswordRequest` (encrypted userName, oldPassword, newPassword), `GetMarginCallByTraderRequest`, `GetUserMessagesRequest` and `getInfoRequest`. The owner confirms **no password was changed**, so TradeCast itself sends this request for some other reason (unknown; possibly a login or session check). Do not call it from the tool until we know what it does.

Capture 3 (one account, three orders, all filled at once; the owner did not place the planned open/cancel orders): SELL 1 GCWL twice and BUY 1 DCR. The sell uses the same 16 pieces; the side piece is `SEL` (not `SELL`). A fill is `ORDER_SENT`, `ORDER_EXE` (`NOR$` = accepted), then `ORDER_EXE_LOG` with `FIL$` (filled). The DCR buy also showed an `ORDER_EXE_LOG` with `NOR$...;QUE` (queued) just before its `FIL$`. One missing-segment gap in this recording.

Captures 4 and 5 (BUY 1 MLCF, limit 100.00 with the market near 107.8): the order stayed **open**. The price is sent as `100.0` (a Java-style double, one decimal) while the server logs `100.00`. Replies: `ORDER_SENT`, `ORDER_EXE` with `NOR$` (accepted), then `ORDER_EXE_LOG` with `NOR$...;QUE` (queued, i.e. open). The counter (piece 12) continued 8, 9, 10 in one TradeCast session, which supports "per-session order counter". Three open orders of this kind were left on the account (the owner could not find how to cancel); no cancel message was ever sent. TradeCast also polls `GetDailyActivityLogRequest` over SOAP about every 30 s.

Still unseen: a **cancel**, a **market** order, a **login** on the trade socket (TradeCast was already logged in each time), and how the **PIN** is carried (the 4 digits before `$TRD` are the same in every order and shown only as `<digits:4>`). Pieces 0, 11, 12, 13 and 14 are unexplained, so building a NEW_ORDER now would still mean guessing at them. That is not allowed by `CLAUDE.md` rule 1.

To confirm what each piece is, a second capture is needed that changes one thing at a time (a SELL, a different quantity and price, and a cancel of an open order).

## Known risks

- The protocol is reverse-engineered and unofficial. AKD can change it, or restrict an account that uses it. Check AKD's terms before relying on it.
- A bug here moves real money. Keep stage 4 tiny and manual.
- Order history decoding is still unverified (`GetOrderHistory` fails), so the tool cannot yet confirm fills or cancels by itself. Check them in TradeCast.
