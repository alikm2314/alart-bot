# Architecture

The current Sanaei, Stars, NOWPayments, and product architecture is documented in [INTEGRATIONS.md](INTEGRATIONS.md). The legacy generic provider contract below describes only the original baseline and must not be used as the Sanaei API contract.

The FastAPI process serves the API, Mini App, standalone shop, and admin interface. PostgreSQL holds the catalog, identities, orders, immutable wallet history, subscriptions, jobs, metrics, and audit records. A worker process polls jobs and Telegram updates, monitors servers, and performs lifecycle maintenance. Caddy terminates HTTPS on one domain. The Mini App uses Telegram signed init data; the panel uses a short lived one-time code delivered to the configured admin ID through the bot.

Wallet purchases lock the wallet row and selected server, calculate the price in the database transaction, write a ledger entry, then create a paid order, pending subscription, and provisioning job. Only the worker can activate a subscription after a successful provider API response. The Sanaei adapter uses a stored deterministic client name and fetches that client on retry to avoid duplicate creation after an ambiguous failure.

## Provisioning API contract

Each server row points to a legitimate operator-controlled HTTPS API and stores a scoped bearer token. `POST /services` accepts `{plan_id, duration_hours, customer_id}` and an `Idempotency-Key` header. It returns `{id, configuration}` only after actual creation. `POST /services/{id}/disable` disables an expired service. `GET /metrics` returns real `cpu_percent`, `ram_percent`, `swap_percent`, `disk_percent`, and `uptime_seconds`; absent metrics are displayed as unavailable. The target API must provide TLS, authentication, request idempotency, and least-privilege permissions. No target API or credential is bundled with this repository.

Trials require an enabled zero-price plan lasting no more than 72 hours. The provider must map that plan ID to limited resources. A trial claim is unique per user and service.

## State

Orders: paid → provisioning → active, or failed. Subscriptions: pending → active, or provisioning_failed; active → expired. Jobs: pending → processing → succeeded, or retrying → processing; after five failures → failed. Wallet transactions are append-only in application code, with a unique reference for idempotency. PostgreSQL constraints reject negative balances and duplicate references.

## Remaining live verification

The Sanaei adapter and payment providers have mocked tests but no live provider tests in this environment. Provider refunds and receipt OCR are not automated. Do not operate this as a fully featured production storefront until the [live checklist](INTEGRATIONS.md) has passed.
