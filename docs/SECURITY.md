# Security

This app handles your brokerage login and live financial data. Read this before pointing it at a real account.

## Threat model

Designed for **one person on their own machine**. The operating-system account is the security boundary. The app has **no authentication**: anyone who can reach the port can read your holdings, and can also trigger a connect/disconnect.

## What it does to protect you

| Concern | Behaviour |
|---|---|
| Network exposure | Binds `127.0.0.1` by default; warns loudly on any other host. No CORS headers are set, so other websites in your browser cannot read the API. |
| Full password on the wire | Never sent. Only 4 characters at 4 random positions, each RSA-encrypted with AKD's public key, as TradeCast's own client does. |
| Password in responses/logs | Never returned by any endpoint, never logged. The login diagnostic log records only length and *which* positions, not the characters. (A test asserts the password is absent from responses.) |
| Password at rest | Fernet-encrypted in `akd_creds.enc`; key in `akd_creds.key` (0600). |
| Account lockout | One login attempt per Connect; a rejection clears credentials and stops, with no retry loop. |
| XSS from broker data | The UI renders with `textContent` only. |
| Writes to the broker | None. Read operations only. |

## What it does NOT protect against

- **Anyone who can read your data directory.** The key sits next to the ciphertext, so the encryption defeats casual disclosure (a backup, a synced folder, a screenshot of the file) but not an attacker running as you. Keep `AKD_DATA_DIR` out of cloud-synced folders and backups you don't control.
- **Other local users / processes** reaching `127.0.0.1:8080`. On a shared machine, don't run it.
- **Unencrypted transport to AKD.** The SOAP endpoint is `http://` (that is what TradeCast itself uses). Only the password characters are RSA-encrypted; holdings and cash travel in cleartext between you and AKD. Use a trusted network.
- **AKD-side detection.** This is an unofficial client. Check AKD's terms of service; automated access may be against them.
- **Protocol change.** The protocol is reverse-engineered; unexpected responses fail closed (nothing stored, `last_error` set), but AKD could change behaviour without notice.

## Hardening checklist

1. Leave `AKD_HOST` at the default. To reach it from a phone/other machine, use an authenticated tunnel (e.g. SSH port-forward or a VPN), never `AKD_HOST=0.0.0.0`.
2. Put the data directory on an encrypted disk (FileVault) and out of synced/backup folders.
3. Use the UI to connect rather than `AKD_PASSWORD` (an env var is visible in process listings and shell history).
4. **Disconnect** when done on a shared machine.
5. Don't commit `data/` (already in `.gitignore`).
6. Never paste real captures into issues/tests; test fixtures here are redacted.

## Reporting

If you find a way credentials or data can leak from this app, fix it or contact the maintainer privately, not in a public issue.
