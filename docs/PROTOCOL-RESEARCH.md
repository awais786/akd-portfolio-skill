# AKD TradeCast / CATALYST protocol — decoded

Captured live (2026-09-18) from a real session by enabling the client's own
Send/Received logging (`WriteLogInClientMachine#true`) with `-Duser.home`
redirected to a writable dir, plus `isDebug#true` stdout. This is the app's own
view of the wire, so message *content* is authoritative; exact on-wire byte
framing still wants a `tcpdump` confirmation (see Open questions).

## The good news

It is **plaintext, pipe-delimited, token-authenticated** — NOT an
RSA-encrypted socket. The only cryptography is on the SOAP login password. That
makes a read-only client genuinely tractable.

## Endpoints

| Server | Host | Port | Role |
|---|---|---|---|
| Login | online.akdtrade.biz | 80 (HTTP/SOAP) | `TradeCastServicev428/LoginServerService?wsdl` |
| Trade | online.akdtrade.biz | 8092 | orders, positions, account |
| Feed | online.akdtrade.biz | 9090 | market data |

## Auth flow

1. **SOAP login** → `LoginServerService`. Response (pipe-delimited):
   ```
   true | 13DO | Trader | true | 0 | Dear Mr. JOHN TRADER | KSE
   ok     token  role     ?      ?   greeting                 exchange
   ```
   The password is almost certainly RSA-encrypted client-side (`RSAKeyGenerator.jar`,
   `iSSecureAuthentication#true`) before the SOAP call. This is the one crypto step.
2. **Socket connect** to 8092 and 9090.
3. **Authenticate** on the socket with the token:
   ```
   MSG-SERVER|AUTHENTICATE_END|true#13DO|*
   ```
4. Server greets:
   ```
   MSG-SERVER|HI_FROM_SERVER|TRADE
   MSG-SERVER|WELCOME|Welcome
   MSG-SERVER|ORDER_NUM|0|*
   MSG-SERVER|ORDER_CATS|CHAT$Offline$|*
   MSG-SERVER|STORED_MESSAGE_END|*
   ```

## Message framing

```
MSG-SERVER|<TYPE>|<payload>|*
```
- Fields delimited by `|`
- Message terminated by `|*`
- Messages concatenated on the stream: `...|*MSG-SERVER|NEXT|...|*`
- Sub-delimiters inside payloads: `;` (records), `:` (fields), `,`, `#`, `$`, `~`
- **`HI_FROM_SERVER` is the heartbeat/keepalive** (64 in a short session) — the
  presence of a raw keepalive strongly implies these log lines are the literal
  wire messages, i.e. the stream is not additionally encrypted.

## Known message types (server → client)

| Type | Meaning | Payload shape |
|---|---|---|
| `HI_FROM_SERVER` | heartbeat / hello | `TRADE` / `Welcome` |
| `AUTHENTICATE_END` | auth ack | `true#<token>` |
| `WELCOME` | session welcome | `Welcome` |
| `ORDER_NUM` | outstanding order count | `0` |
| `ORDER_CATS` | order categories | `CHAT$Offline$` |
| `STORED_MESSAGE_END` | end of backlog | — |

## Feed protocol (9090)

```
MKT-FEED|FCCL:REG;LPCL:REG;PIAA:REG;...     subscribe market watch (SYMBOL:BOARD)
MBP-FEED|FCCL:REG                            subscribe Market-By-Price (depth)
MBO-FEED|FCCL:REG                            subscribe Market-By-Order (depth)
```
Boards seen: `REG` (regular). Others exist (FUT, ODL, CSF…) per the market data doc.

## Reference data

- **Account**: `COAF00000;JOHN TRADER;;1;Normal;0000000000000`
  → `accountNo;name;;?;type;key`
- **Circuit caps**: `SYMBOL-EXPIRY;BOARD;upperCap;lowerCap;` joined by `#`
  e.g. `ACPL-CSEP;CSF;253.8800;207.7200;#AGP-CNOV;CSF;171.1300;140.0100;#...`
- **User's portfolio-watch symbols** (this session): ATRL, DCR, FCCL, FFC,
  HUBC, KTML, MARI, MEBL, MUGHAL, OGDC, SYS.

## Open questions (need more capture)

1. **Exact wire framing** — confirm with `sudo tcpdump -A -i any host online.akdtrade.biz and port 8092`
   whether the bytes match these log lines (plaintext) or are wrapped/encrypted.
2. **SOAP login** — capture the HTTP request to `LoginServerService` (it's port 80,
   so a proxy or tcpdump shows it) to see the RSA-encrypted password field and
   replicate it. This is the gating step for an independent client.
3. **Positions/holdings format** — not captured this session (0 orders; the
   Portfolio Watch "btndetail" click did not emit a holdings record to the logs).
   Open a Custody / Net Position / Account Statement view and re-capture.
4. **Order placement format** — map from a real order capture, watched, never
   tested blind. Wire it behind the existing SignedOrderIntent boundary.

## Build path (read-only first)

`psx_core/trading/akd_socket.py` (future):
1. SOAP login → token (needs RSA password step — item 2 above)
2. connect 8092/9090, send `MSG-SERVER|AUTHENTICATE_END|true#<token>|*`
3. read `|*`-framed messages, parse by TYPE
4. subscribe `MKT-FEED|<sym>:REG;...`, stream live prices → platform
5. request positions (format from item 3) → platform portfolio, live

Order entry (item 4) is a later, safety-gated phase.
