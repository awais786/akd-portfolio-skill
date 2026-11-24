# Capturing one real order (to learn AKD's order message)

Goal: record the traffic of the **official TradeCast client** while you place **one tiny order**, so the layout of the order message can be worked out from real evidence instead of a guess. Nothing in this app sends anything: the recording is passive.

**Read this first**
- The recording file contains your **trading PIN** and session details. Keep it private, never send or upload it, and **delete it when you're done** (step 6).
- The report tool prints a *redacted* summary: your PIN shows as `<digits:4>`, your account id and session token as `<masked>`. It has no option to print raw values. Glance through the report before pasting it anywhere.
- You will place **one real order** on your real account. Make it as harmless as possible (below). You are responsible for it: check it in TradeCast and cancel it.

## The order to place

- **One BUY of 1 share** of a liquid stock (for example OGDC).
- A **limit price about 3-5% BELOW the last price**, so it will not fill. It must be inside PSX's daily price band or it may be rejected before it even shows the message you need (the rejection is still useful, but a normal accepted order is best).
- Place it **during market hours** (Mon-Fri; PSX regular session), so it is accepted normally.
- Write down exactly: the **symbol, side, quantity and limit price** you used. The tool needs them to recognise the message.
- Afterwards, **cancel it** in TradeCast. Cancelling is worth capturing too.

## Steps

1. **Find your network interface** (on this Mac it was `en0`):
   ```bash
   route -n get online.akdtrade.biz | awk '/interface:/{print $2}'
   ```
2. **Start the recording BEFORE you open TradeCast** (it needs your Mac password; leave this Terminal window running):
   ```bash
   sudo tcpdump -i en0 -s 0 -w ~/akd-order.pcap host online.akdtrade.biz
   ```
   (In Claude Code you can run it with the `!` prefix: `! sudo tcpdump -i en0 -s 0 -w ~/akd-order.pcap host online.akdtrade.biz`, but it must keep running, so a separate Terminal window is easier.)
3. **Use TradeCast normally:** open it, log in, wait a few seconds, place the one order above, wait until it shows as open, then cancel it, wait a few seconds, log out and close TradeCast.
4. **Stop the recording:** go back to the Terminal running `tcpdump` and press **Ctrl+C**.
5. **Make the redacted report** (fill in your real values; `ACCOUNT` is your AKD account id, so it is masked everywhere):
   ```bash
   cd akd-app
   make capture-report PCAP=~/akd-order.pcap SYMBOL=OGDC SIDE=BUY QTY=1 PRICE=150.00 ACCOUNT=COAF00000
   ```
   Read it. It should list message types, an **ORDER CANDIDATES** section showing the message that contained your symbol/side/quantity/price, and the replies from AKD. Copy the whole report into the chat.
6. **Delete the recording:** `rm ~/akd-order.pcap`

## If something goes wrong

| Report says | Meaning / fix |
|---|---|
| `no TCP packets` | Nothing was recorded. Check the interface name (step 1) and that `tcpdump` was running when TradeCast was used. |
| `no traffic on the trade socket (port 8092)` | TradeCast was not logged in during the recording, or it connects somewhere else (or over TLS). Tell me what the report's other sections show. |
| `ORDER CANDIDATES ... none found` | The symbol/side/qty/price you passed don't match what you typed in TradeCast exactly, or the order wasn't placed while recording. |
| `pcapng` error | Re-record with the plain command above (classic pcap). |
| `unsupported capture link type N` | Tell me the number `N`; use `-i en0` rather than `-i any`. |
| `missing-segment gaps: N` (N > 0) | Some packets were lost while recording; the layout may still be readable. Record again if the order message looks cut off. |

## What happens next

The report shows the order message's type name, the position of each field and AKD's replies. What we learned and what is still missing is in [ORDER-ENTRY.md](ORDER-ENTRY.md), which also has the stages (decode, offline tests, a tiny live test placed by **you**).

Limits of this tool: it has read real macOS recordings made with `-i en0` (plain Ethernet). IPv4 and plain TCP only; the trade socket (8092) and the web service (80) are the only ports it reads. Words it does not know are shown as shapes (`<text:N>`); add a harmless one to `KNOWN_WORDS` in `src/akd/capture.py` to see it.
