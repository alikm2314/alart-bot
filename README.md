# Alart

Alart is a Telegram service storefront with a Mini App, bot, operations panel, PostgreSQL ledger, and background provisioning worker.

The existing baseline has been extended with managed Sanaei panels, product and inbound mappings, server-side custom pricing, Telegram Stars, NOWPayments, and manual payment review. External services are **NOT LIVE-TESTED** in this environment. See [INTEGRATIONS.md](INTEGRATIONS.md) and [DEPLOYMENT.md](DEPLOYMENT.md) before accepting customers.

The requested physical-device limit is **not implemented**. Current plan limits map to Sanaei's concurrent source-IP limit; the Mini App now labels this explicitly. Read [DEVICE_LIMIT_FEASIBILITY.md](DEVICE_LIMIT_FEASIBILITY.md) before advertising any plan as device-limited.

The current development pass added shared Persian/English text and RTL handling, qualified referral rewards, real panel traffic sampling, private connection QR codes, secure manual receipt upload, business finance reports and exports, Telegram topic event delivery, and explicit free and unlimited limits. Encrypted backups now include private receipts. See [PRODUCTION_READINESS.md](PRODUCTION_READINESS.md) for remaining gaps. This is **not production-ready**; the VPS, new database migrations, live payments, Sanaei operations, topic delivery, and restore process have not been live-tested.

## Local development

Copy `.env.sample` to `.env`, fill the required variables, and generate `Caddyfile` from `Caddyfile.template` with your domain before running `docker compose up --build`. The API serves `/app/`, `/panel/`, and `/api/` behind Caddy. Run `python -m unittest discover -s tests` after installing the Python requirements.

## Components

- `alart/`: API, Telegram authentication and bot webhook, financial ledger, provisioning and monitoring workers.
- `web/app/`: fast, dependency-free Mini App.
- `web/panel/`: admin operations interface.
- `db/`: versioned SQL migration.
- `install.sh`, `update.sh`, `backup.sh`, `restore.sh`: VPS lifecycle scripts.

Deployment requires a Telegram bot token, a Telegram admin ID, a domain pointing to the VPS, and a Sanaei bearer API token added later through the admin panel. Optional AI requires a separate OpenAI developer API key. See [INTEGRATIONS.md](INTEGRATIONS.md), [ARCHITECTURE.md](ARCHITECTURE.md), [SECURITY.md](SECURITY.md), and [OPERATIONS.md](OPERATIONS.md).
