# Security

This tool handles your brokerage login. Read this before pointing it at a real account.

- **Unofficial.** It speaks AKD's TradeCast protocol as reverse-engineered from the client. AKD may object to or change it. Use only your own account.
- **Read-only.** Only the read operations (`GetCollateral`, `GetExposureDynamic`, `GetOrderHistory`) are sent. No order is ever sent.
- **Password handling.** Supplied through the environment (`AKD_PASSWORD`), held in memory for one run, never written to disk, never logged, never printed, never in an error message. Only 4 positional characters (RSA-encrypted with AKD's public key) go on the wire. Do not put it in `.env`, in a command line, or in chat. Type it into your own shell, e.g. `read -s AKD_PASSWORD && export AKD_PASSWORD`.
- **One attempt.** AKD locks accounts after repeated bad logins. The command makes exactly one login attempt per run and stops on rejection. Don't loop it.
- **Transport.** AKD's SOAP endpoint is plain HTTP; the password never crosses it in full (positional, RSA-encrypted), but treat the account data as visible to your network.
- **Output is sensitive.** The JSON holds your positions and cash. Don't paste it where you wouldn't paste a statement.
- **Fixtures and captures.** Never commit real account data, tokens, or `.pcap` files (they contain the trading PIN).
