# Customer-path repair verification — 2026-09-25 (0.2.1)

Current verification supersedes the historical audit below. It is not a claim that every possible bug is eliminated.

- 121 Python tests and five Node suites pass. QR images are decoded back to their exact URLs using zxing-cpp, including long URLs.
- Nine isolated acceptance scenarios passed on the configured VPS and actual Sanaei panel: migrations twice, real trial creation and subscription-page HTTP 200, duplicate trial rejection, paid QA-wallet order retry, renewal of the same client/subscription URL, localized time/traffic notice deduplication, expired access rejection, confirmed remote deletion/archive, guide editing and invalid-video rejection, and manual receipt upload failure/retry/resubmission. The nine scenario groups include related assertions. No real money was charged; Telegram sends were captured. QA clients and database were removed.
- Bootstrap API timing in the container: 15.8–22.1 ms in the last five-request run. This excludes network, Telegram and phone rendering. It does not establish a two-second end-to-end mobile launch guarantee.
- Blocking Telegram SDK loading was removed; authenticated startup uses signed launch data and one bundled bootstrap response. Assets can be cached, QR images load on demand, and the former per-pixel QR background computation was removed.
- Subscription URLs use Sanaei's configured subURI and the client's subId. Protocol UUIDs and management URLs are not guessed as subscription links. Renewal preserves provider identity.
- Service status derives from actual expiry, sampled quota and confirmed remote deletion. Panel reconciliation runs approximately every five minutes, subject to availability; failed calls never prove deletion. Ended trials archive after 48 hours, paid ended services remain renewable unless removed at the provider. Users can hide ended services themselves.
- Time reminders: within seven days, one day and one hour; low traffic at the lesser of 1 GB or 20%; ended notices are deduplicated. Worker delivery and the user not blocking the bot are required.
- Connection guides: owner panel → Administration → Connection guides. Separate Persian/English text and MP4/WebM video up to 50 MB for iOS, Android, Windows and macOS. Guide videos are included in private-data backups.
- Mobile fixture UI was inspected in Persian: wallet home, pending/active/expired services, visible copy control, matching QR copy action, and device guide navigation. A fixture is not an authenticated Telegram device session.

Remaining acceptance boundaries: genuine Telegram Stars payment/refund, actual message delivery on the user's Telegram devices, video playback with owner-uploaded content, connection through a real VPN client, full owner-panel visual review, and encrypted restore were not exercised in this pass. Network and device performance need measurement on the user's connection.

## Historical audit (earlier runs; not current status)

# Alart readiness audit — 2026-09-17

## Update — 2026-09-19

The Admin Panel now has a separate sign-in shell. Management navigation is hidden in the HTML until `/api/me` confirms an administrator role; the authenticated shell has icon navigation, a dedicated sign-out control, a rebuilt responsive stylesheet, and focused payment views. This is a visual access boundary only; every API still enforces server-side authorization. The signed-out Persian and English login screen was visually checked in dark and light themes on the live site. Authenticated pages still require a live administrator login for visual acceptance. The current suite is 108 Python tests and 3 Node tests.

The release/update workflow has now been exercised against the connected VPS. The updater created encrypted backups before deployment, preserved `.env` and Docker volumes, restarted API and worker, and the public `/api/health` endpoint returned `ok`. This checks deployment and API startup, not payment or provisioning correctness.

The Admin Panel now loads only the selected section, offers search results instead of navigating while the operator types, guides free test offer creation into inbound mapping, and displays real host metrics from Sanaei `/panel/api/server/status` plus node metrics from `/panel/api/nodes/list`. Samples older than five minutes are marked stale. Missing values stay unavailable; Sanaei's node list does not provide node swap or disk usage. Client traffic parsing now follows the documented nested `traffic` object. One new mocked provider test checks host/node metrics, and another checks nested client counters. The current suite is 107 Python tests and 2 Node tests, all passing locally.

**Still not production-ready:** the operator-authenticated panel screens, Sanaei metrics/node API on the actual configured panel, a real client creation/renewal/disable, a genuine payment/refund, and an encrypted restore have not been verified in this pass. The current visual redesign is incremental; full hands-on UX review of every Admin Panel section is still required. Do not infer working live Sanaei access from a healthy Alart API.

**Status: NOT PRODUCTION-READY.** This Windows workspace has no live PostgreSQL, Docker engine, VPS, Telegram Stars transaction, Sanaei panel, NOWPayments account, or private Telegram topics. Automated tests use fakes for external integrations. No production database, `.env`, Caddy file, or Docker volume was reset or removed.

## Existing baseline before this continuation

The API, bot, Mini App, admin panel, PostgreSQL migrations, managed Sanaei panels, product and inbound mappings, pricing, Stars and NOWPayments flows, wallet ledger, manual review, worker retries, expiry, monitoring, installer, encrypted backup and release/update workflow were already present. Earlier local tests numbered 39. A later interrupted pass had begun localization and referral work.

## Implemented or extended locally

- Shared Persian/English Mini App, web shop, and admin translations with RTL/LTR switching; bot and notification translations. Some dynamic and backend errors still lack complete Persian coverage.
- Referral codes, first verified paid purchase and successful provisioning qualification, configurable default rewards, idempotent wallet credits, notifications, and refund reversal or wallet freeze when the reward cannot yet be recovered.
- Real Sanaei traffic counters with timestamps, owner-scoped usage API, no fabricated counters, and remote disable on measured traffic exhaustion.
- Private owner-scoped QR image for the first real connection link, copy/manual instructions, and best-effort Hiddify import. A true subscription URL is only available if the provider returns one; none is generated artificially.
- Private receipt uploads, type/signature/size checks, operator-only download, audited manual review, and a persistent private Docker volume. OCR is not implemented and receipts never settle payments.
- Business finance entries, revenue/refund/expense/fee/manual-sale views, date filters, and CSV/XLSX exports. These records are separate from customer wallets. Manual sales are accounting entries only.
- Durable private Telegram topic events by configured category, with fixed labels and numeric IDs. Delivery requires real group/topic configuration and has not been live-tested.
- Explicit one-time free wallet plans and zero limits: traffic 0 means unlimited, user limit 0 means unlimited concurrent IPs, duration 0 means no expiration. Timed volume plans still require a positive duration; trials still require positive traffic and duration.
- Security fixes for bot group-chat privacy, Sanaei URL/private DNS restrictions, legacy server token encryption, receipt access, rate limiting, secret-bearing HTTP logs, CSV formula safety, and Docker process privileges. Database pools default to four API and two worker connections in Compose.
- `cryptography` requirement raised to the patched 50.0.1 series; local `pip-audit` found no known vulnerabilities after updating the local virtual environment and image pip requirement.
- Encrypted backups now include the private receipt volume. Release archive packing was smoke-tested; it excluded `.env` and included new migrations and code.
- Same-subscription renewal now has server-side quotes, a direct bot reminder action, Mini App checkout, a durable absolute target for worker retries, and admin renewal history. Migration `014` adds renewal targets and refund-only review for failed renewals. A terminal ambiguous panel failure requires operator reconciliation before refunding.

## Local verification

- 96 Python unit tests and 2 Node localization/money smoke tests pass in this local workspace. Renewal tests cover time and traffic calculations, ownership, price and request-key protection, verified settlement, retry target reuse, notification action, and refund-only preservation of the existing service. The full frontend syntax audit should be rerun for every release.
- External integration tests use mocked HTTP responses. No new PostgreSQL migrations have run against a real database here.
- XLSX archive/XML shape and release package contents were inspected locally. The finance export has not been opened in a real Excel client.
- `pip-audit --local` returned no known vulnerabilities for the updated local environment on this date. Container images and Ubuntu packages have not been scanned.
- A deliberately simulated expiry failure logs an error during the successful test suite; that output is expected by the test.

## Remaining limitations and deployment gates

- Full Persian translation is unfinished for every dynamic error and report. Verify both themes and RTL/LTR visually on real devices.
- No universal client import is promised; other app deep links and real subscription URLs need provider/client-specific validation.
- Referral reversal is tied to a confirmed refunded order. Customer cancellation and refund review are implemented, with provider confirmation required before an external refund is marked complete. An ambiguous renewal panel outcome requires operator reconciliation; automatic refund is intentionally withheld.
- The finance view is a cash-flow report, not a tax ledger. Exports are capped at 10,000 rows and require narrowing the date range beyond that.
- Topic delivery is at least once: Telegram can accept a send before the database records delivery, so an occasional duplicate topic message is possible.
- Current rate limits are per process; there is no shared/distributed limiter. DNS rebind between preflight resolution and the actual connection remains a residual SSRF risk on operator-configured URLs.
- Customer connection strings remain plaintext in the live PostgreSQL subscription table so the API can deliver them. Database and host access must be restricted; application-level encryption of those strings has not been added. Encrypted backups cover the database at rest outside PostgreSQL.
- No live restore test, migration test, external-provider test, load test, Docker Compose test, or VPS update test was possible here. Do not call the system production-ready.
- Optional receipt OCR, automated NOWPayments/manual refunds, wallet top-up checkout, and complete admin manual-service-sale provisioning remain unimplemented.

## Required live acceptance checks

1. On a disposable copy of the target Ubuntu 22.04/24.04 VPS profile (1 CPU, about 2 GB RAM, 4 GB swap, about 24 GB disk), run the installer and then the release update. Verify `.env`, Caddy, PostgreSQL and private receipt volumes survive.
2. Apply migrations to a copy of the existing database and inspect constraints, legacy token encryption, order/wallet counts, and startup logs. Restore an encrypted database and private-data backup on an isolated host.
3. Add real Sanaei panels, discover and map inbounds, create/update/disable a client, test traffic reporting and exhaustion, renewal, trial, expiry, multiple panels, and remote failure retry/idempotency.
4. Complete genuine Telegram Stars and NOWPayments test payments and duplicate callback checks; exercise manual review and receipt permissions. Reconcile amounts and refund behavior with the providers.
5. Configure a private Telegram supergroup with topics; verify each configured topic and the backup completion message. Test customer notifications in Persian and English.
6. Run representative mobile UI, concurrency, security, and memory/load checks on the actual 1-core VPS before accepting paying customers.
