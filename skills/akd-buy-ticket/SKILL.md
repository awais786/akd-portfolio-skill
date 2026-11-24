---
name: akd-buy-ticket
description: Draft and check a BUY ticket (symbol, quantity, limit price, account) for the user's AKD brokerage account, using their already-fetched portfolio. The user places the trade themselves in AKD's app; nothing is ever placed. Use when the user wants to buy, add to, or average down a stock, or says "make a buy ticket".
---

# AKD buy ticket

Checks a **buy idea** against one account and writes it up as a ticket. **It cannot place orders and never will.** The user enters the trade in AKD's own app. Never ask for, accept or repeat a trading PIN.

Usage: `/akd-buy-ticket 100 OGDC 315 COAF00000` (quantity, symbol, limit price, account), or plain words.

## Steps

1. **Collect four things:** the account, the symbol, how many shares (a whole number) and the **limit price** (the most the user will pay, at most 2 decimals). Limit orders only: if they ask for a market order, explain and ask for a limit price. If anything is missing, ask for it in one short question.
2. **Get that account's facts from the portfolio data already in this conversation:** its free cash (`cash.data.cash.free_cash_pkr`) and AKD's price for the stock (`market_price`, if the account already holds it). If the data is not in the conversation, **ask before fetching**: say the user can run `/akd-portfolio` first (it logs in once per account), or just tell you the free cash and AKD's price. Never log in silently, and never run the portfolio fetch twice.
3. **Run the check** (offline, no login):

```bash
~/.claude/skills/akd-portfolio/run.sh ticket buy --symbol OGDC --qty 100 --price 315.00 \
    --free-cash 100000 --ref 315.87 --ref-source "AKD mark" --account COAF00000
```

   The user may get an approval prompt showing the order (a hook): that is expected, never work around it. Exit 0 = allowed, 1 = a check failed, 2 = bad input (fix it and ask).
4. **Report in this shape:**
   - **The ticket:** `BUY <qty> <symbol> @ Rs <price> (limit) = PKR <total> | account <id>`.
   - The checks that ran (order size, price sanity, enough cash incl. the fee allowance) and their result. If one failed, say which and why, and do **not** present the ticket as ready. Never quietly change the quantity or price to make it pass: say what would pass and ask.
   - **Concentration warning (advice, not a check):** work out the stock's weight in that account after the buy. If it would be above 25%, say so. If the account already holds the stock and it is down more than 15%, say that adding to it is averaging down into a loss, and that it makes the position bigger.
5. **End with:** costs (brokerage) and capital gains tax are not included, and "**Nothing was placed: you place this yourself in AKD's app.**"

These checks only catch typos and obvious mistakes. They are not advice: say if you have no opinion on whether the user should buy.
