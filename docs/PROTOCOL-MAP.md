# AKD TradeCast — protocol map (reverse-engineering)

Purpose: document AKD's TradeCast wire protocol well enough to talk to the
broker directly from the platform and retire the passive TradeCast capture.
Legitimate interop work on the account holder's own client. Source: the
installed Java client at `~/AKD-TradeCast` (client v3.7.7, service v428) plus
the existing SOAP/socket captures. No credentials appear in this document.

## Confidence legend
- **SPEC** — fully recovered from client bytecode; reproducible now.
- **CAPTURE** — validated against real captured traffic.
- **MODEL** — data model known; exact wire layout still to confirm.
- **PENDING** — identified but not yet mapped.

## 1. Transports

The client speaks three channels to `online.akdtrade.biz`:

| Channel | Port | Protocol | Carries |
|---|---|---|---|
| SOAP web service | 80 (HTTP) | `TradeCastServicev428/LoginServerService` | login, account reads, statements, logs |
| Trade socket | 8092 | line protocol, XOR-0x1D | session auth, order entry, order/trade events |
| Feed socket | 9090 | line protocol, XOR-0x1D | market data / quotes |

Launch config is `~/AKD-TradeCast/args.txt` (`WebServiceURL`, `tradeCastIP`,
`tradeCastServerPort#8092`, `feedServerPort#9090`, `ServerCode#03`,
`iSSecureAuthentication#true`, `isUseSecondaryPassword#false`).

## 2. SOAP login — `SecurecustomAuthentication`  [SPEC]

Endpoint: `POST http://online.akdtrade.biz/TradeCastServicev428/LoginServerService`

Four string parameters, **each independently RSA-encrypted then base64'd**:

    field = Base64( RSA/ECB/PKCS#1v1.5_encrypt( plaintext, public.key ) )

- `UserName` — the username (raw)
- `Password` — the password (raw)
- `PasswordPosition` — see §4 (empty/positional; confirm on build)
- `ServerCode` — the constant `"03"` (from args.txt)

Cipher recovered from `CATALYST.Security.Encrypter.rsaEncrypt`
(`Cipher.getInstance("RSA")` → RSA/ECB/PKCS1Padding; commons-codec Base64).
The wrapper `CATALYST.Socket.EncryptDecrypt.EncryptText` just delegates to it.

Public key: `public.key` resource in `TradeCast.jar`, a Java-serialised pair
of `BigInteger`s (modulus, exponent). **Extracted: 512-bit modulus,
exponent 65537.** It is the server's public key — safe to embed and reuse;
no server-side key exchange precedes login (only a bare `test` ping).

Note: RSA-512 is weak by modern standards — AKD's choice; irrelevant to
interop, but worth recording. PKCS#1 v1.5 padding is randomised, which is why
each captured login ciphertext differs; we supply that randomness ourselves.

Response: `SecurecustomAuthenticationResponse` — parse for session validity /
account list (structure in the captured resp; wire the parser on build).

## 3. Socket line protocol  [CAPTURE]

Every byte on both sockets is **XOR 0x1D** on the wire. Decoded, messages are:

    MSG-CLIENT|<TYPE>|<field>|<field>|...*

- `|` field delimiter, `~` sub-list delimiter, `*` message terminator.
- `MSG-CLIENT` XOR 0x1D = `PNZ0^QTXSI`; `|` = `a` (validated).

Client→server message types observed in capture:
`HI_FROM_CLIENT` (heartbeat), `NEW_CLIENT`, `NEW_CLIENT_RETRY`, `LOCALIP`,
`AUTHENTICATE_PARTIAL`, `STORED_MESSAGE_LIST`.

Transport class: `CATALYST.Socket.MyTcp` — `SendMessage(String[])` +
`FormatMessage(String[])` (joins with `|`, wraps `MSG-CLIENT…*`), XOR via
`CATALYST.Socket.Security`. Inbound via `MessageRecieveListner`.

## 4. Socket session auth — `AUTHENTICATE_PARTIAL`  [CAPTURE / MODEL]

Shape (values redacted):

    MSG-CLIENT|AUTHENTICATE_PARTIAL|<account>|<token>|<p1~p2~p3~p4>|*

The `~`-delimited tail is a **positional element** (a partial-credential
position list) — this, not the SOAP `PasswordPosition`, is where the position
scheme actually bites. Resolve the exact derivation from `LoginForm2` on build.

## 5. Account reads — SOAP  [SPEC — parsers largely exist in repo]

Operations captured on `LoginServerService` (all follow the `<return>…</return>`
envelope the repo's `soap.extract_return` already unwraps):
`TradAccounts`, `getInfo`, `GetTCSetting`, `getCompressedData`, `MarketStatus`,
`GetCollateral`, `GetAccountStatement`, `GetOrderHistory`, `GetTradeLog`,
`GetDailyActivityLog`, `GetOutstandingLog`, `GetExposureDynamic`,
`GetUserMessages`, `GetMarginCallByTrader`, `SaveMyProfileDB`.

`GetCollateral` / `GetOrderHistory` / `GetAccountStatement` response parsers
already live in `packages/trading/src/psx_trading/akd/parse.py`. The rest are
new but same-shape.

## 6. Order entry — trade socket  [MODEL — wire layout PENDING]

Order model `Beans.BuySellOrder`:
`Market, Symbol, BuySellType, WinType, OrderType, Volume(int), Price(double),
Account, PinCode, Limit(double), uppercap(double), lowerlock(double)`.

Placement path: `BuySellOrder` → `String[]` → `MyTcp.FormatMessage` → XOR →
trade socket (8092). Requires the trading **PIN** (`PinCode`).

**Gap:** no order was placed during any capture, so the exact
`MSG-CLIENT|<ORDER_TYPE>|…*` field order is not in the traffic. Two ways to
close it: (a) decompile the obfuscated `Orders/` package where the message is
assembled, or (b) capture one real order placement. (a) is deterministic and
needs no live trade.

## 7. Market feed — socket 9090  [PENDING]

Same XOR-0x1D line protocol on the feed socket; message catalogue not yet
enumerated. Repo already has raw feed captures under `/tmp/tclog/proxy`.

## 8. What replacing TradeCast takes

1. **Login** (§2) — ready to build; all parameters recovered.
2. **Socket session** (§3–4) — connect 8092, XOR framing, `NEW_CLIENT` +
   `AUTHENTICATE_PARTIAL` handshake, `HI_FROM_CLIENT` heartbeat.
3. **Reads** (§5) — SOAP request builders (new) + existing parsers.
4. **Order entry** (§6) — close the one wire-layout gap, then build the
   `MSG-CLIENT` order message. This is the SECP-gated, money-moving surface.
5. **Feed** (§7) — enumerate feed messages if we want live quotes off-PSX
   (spec says PSX stays the primary quote source, so this is optional).

No blocker found. The only non-trivial remaining decode is the order message
layout (§6) and the exact position derivation (§4), both in-client and
recoverable without a live trade.
