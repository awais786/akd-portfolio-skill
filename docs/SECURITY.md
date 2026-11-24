# Security

This tool handles your brokerage login. Read this before pointing it at a real account.

- **Unofficial.** It speaks AKD's TradeCast protocol as reverse-engineered from the client. AKD may object to or change it. Use only your own account.
- **Read-only.** Only the read operations (`GetCollateral`, `GetExposureDynamic`, `GetOrderHistory`) are sent. No order is ever sent.
- **Password handling.** Supplied through the environment (`AKD_PASSWORD`) or the repo's git-ignored `.env`. The tool holds it in memory for one run and never writes it anywhere, never logs it, never prints it, never puts it in an error message. Only 4 positional characters (RSA-encrypted with AKD's public key) go on the wire. If you use `.env`, it is the one place the password sits in plain text: keep it on a machine you trust, `chmod 600 .env` (the tool warns if other users can read it), and never copy it, paste it, commit it or put it in a command line or in chat. To avoid a file entirely, type it into your own shell: `read -s AKD_PASSWORD && export AKD_PASSWORD`.
- **One attempt.** AKD locks accounts after repeated bad logins. The command makes exactly one login attempt per run and stops on rejection. Don't loop it.
- **Transport.** AKD's SOAP endpoint is plain HTTP; the password never crosses it in full (positional, RSA-encrypted), but treat the account data as visible to your network.
- **Output is sensitive.** The JSON holds your positions and cash. Don't paste it where you wouldn't paste a statement.
- **Fixtures and captures.** Never commit real account data, tokens, or `.pcap` files (they contain the trading PIN). See `docs/CAPTURE-GUIDE.md`.
