---
name: akd-sell-ticket
description: Draft and check a SELL ticket (symbol, quantity, limit price, account) for the user's AKD brokerage account, using their already-fetched portfolio. The user places the trade themselves in AKD's app; nothing is ever placed. Use when the user wants to sell or trim a stock, or says "make a sell ticket".
---

# AKD sell ticket

Checks a **sell idea** against one account and writes it up as a ticket. **It cannot place orders and never will.** The user enters the trade in AKD's own app. Never ask for, accept or repeat a trading PIN.

Usage: `/akd-sell-ticket 500 MLCF 105 COAF00000` (quantity, symbol, limit price, account), or plain words.

## Steps

1. **Collect four things:** the account, the symbol, how many shares (a whole number) and the **limit price** (the lowest price the user will accept, at most 2 decimals). Limit orders only: if they ask for a market order, explain and ask for a limit price. If anything is missing, ask for it in one short question.
2. **Get that account's facts from the portfolio data already in this conversation:** the shares held of that symbol (`holdings.data.positions[].quantity`) and AKD's price for it (`market_price`). If the data is not in the conversation, **ask before fetching**: say the user can run `/akd-portfolio` first (it logs in once per account), or just tell you the held quantity and AKD's price. Never log in silently, and never run the portfolio fetch twice.
3. **Run the check** (offline, no login):

```bash
~/.claude/skills/akd-portfolio/run.sh ticket sell --symbol MLCF --qty 500 --price 105.00 \
    --held 1900 --ref 103.93 --ref-source "AKD mark" --account COAF00000
```

   The user may get an approval prompt showing the order (a hook): that is expected, never work around it. Exit 0 = allowed, 1 = a check failed, 2 = bad input (fix it and ask).
4. **Report in this shape:**
   - **The ticket:** `SELL <qty> <symbol> @ Rs <price> (limit) = PKR <total> | account <id>`.
   - The checks that ran (order size, price sanity, shares held) and their result. If one failed, say which and why, and do **not** present the ticket as ready. Never quietly change the quantity or price to make it pass: say what would pass and ask.
   - If it is a trim, show the arithmetic: shares held, after the sale, and the weight of the stock in the account before and after.
5. **End with:** costs (brokerage) and capital gains tax are not included, and "**Nothing was placed: you place this yourself in AKD's app.**"

These checks only catch typos and obvious mistakes. They are not advice: say if you have no opinion on whether the user should sell.
