# Security

Telegram Mini App init data is verified server-side using Telegram's HMAC algorithm and a ten minute age limit. Session cookies are `HttpOnly`, `Secure`, and `SameSite=Strict`. State-changing authenticated requests check `Origin` against the configured domain. Admin panel codes are random, hashed at rest, single-use, and expire after five minutes. Admin routes require an owner or operator role.

The database, worker, and API have no public ports. Only Caddy exposes 80/443. Provider and OpenAI tokens remain server-side. The Docker API image runs as a non-root user. `.env` is excluded from Git and created mode 600. The operator should limit access to Docker and database administration and rotate credentials after compromise.

Authentication, assistant, and manual receipt endpoints have basic per-process IP throttling. Sanaei, legacy server, and NOWPayments credentials are encrypted at rest with `PANEL_ENCRYPTION_KEY`; keep this key separate from database backups. New legacy server writes are encrypted, and startup migrates existing plaintext tokens in a database transaction. Panel URLs require HTTPS and public DNS results; redirects and environment proxies are disabled for outbound panel calls. Customer bot commands are private-chat only. Uploaded receipts are capped at 3 MB, checked by media type and image signature, stored outside the web root in `private_data`, and downloadable only by operators. They never approve payment.

Known gaps: no distributed rate limiter, no per-user support quota, a DNS rebinding interval between preflight lookup and outbound connection, and no external security review. The built-in wallet deposit endpoint is an audited operator action and must be used only after an independently verified settlement. NOWPayments callbacks are signed and verified through the provider API; their live behavior is **NOT LIVE-TESTED**. The local dependency audit on 2026-09-17 found no known vulnerabilities after updating `cryptography` and `pip`; container base images and Ubuntu packages still require live scanning.

Subscription connection strings are currently plaintext in PostgreSQL for authenticated delivery. Restrict database and host access and treat database dumps as sensitive; the backup archives are encrypted. Application-level encryption of these strings remains a future hardening task.

## Audit by area

| Area | Local finding / control | Remaining verification |
| --- | --- | --- |
| Telegram WebApp auth | Signed initData HMAC and age checked; tampering tests pass | Real Telegram login on target devices |
| Sessions and admin roles | Secure, HttpOnly, SameSite cookie; owner/operator/viewer checks; one-time login codes | Cookie behavior in deployed Telegram webview |
| IDOR and cross-user data | Account, order status, usage, and QR queries scope by authenticated user ID | Live API probing with two accounts |
| Stars and NOWPayments | Pre-checkout does not settle; successful payment/verified finished status required; signed IPN and amount/order match | Genuine duplicate provider updates and refund reconciliation |
| Wallet and referrals | Wallet row locks, unique transaction references, qualified referral row lock, reversal freeze | Concurrent PostgreSQL load test and Sybil-abuse policy |
| SQL and commands | Query values parameterized; dynamic column sets derive from Pydantic models; shell inputs quoted/validated | Independent code review and shellcheck on Ubuntu |
| SSRF and redirects | Operator panel URLs require HTTPS/public DNS; outbound redirects/proxies disabled | DNS rebinding-resistant transport or network egress policy |
| Files and paths | Private receipt names generated; 3 MB cap, MIME/signature checks, owner/operator scopes; release archive rejects traversal | Malware scanning policy and private-volume restore test |
| AI assistant | Server-side key, user-scoped context, no write tools or raw database access | Prompt-injection evaluation with live API key |
| Headers and Docker | CSP, HSTS, nosniff, no public DB port; non-root API/worker with dropped capabilities | `docker compose config`, container/image scan, live TLS check |
| Error and secret leakage | External provider errors sanitized; HTTP request INFO logging disabled to protect Bot API token URLs | Review actual VPS logs under failure conditions |
| Rate limiting | In-process limits on auth, assistant, and manual payment paths | Shared limiter for multiple API replicas |
