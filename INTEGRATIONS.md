# Alart integration and live deployment guide

**Status: NOT LIVE-TESTED.** The test suite uses mocked external services. Compatibility with the operator's actual Sanaei panel, Telegram Stars account, NOWPayments account, and VPS is unverified. Do not accept paying customers until controlled live tests pass.

## Provisioning

An admin adds a Sanaei panel under **Panels** with a display name, HTTPS URL, bearer API token, location, and operator-defined capacity. Credentials are encrypted with `PANEL_ENCRYPTION_KEY`. Test connection to discover inbounds, explicitly allow selected inbounds, then map products. Only healthy enabled panels and mapped enabled inbounds are eligible for checkout. The worker creates a remote client using the [Sanaei client API](https://docs.sanaei.dev/docs/reference/api/clients/), stores its immutable Alart mapping, and fetches real configuration links. Renewal updates the client; expiration and traffic depletion disable it remotely before updating Alart state. Panel capacity is operator configured and is not a measured Sanaei metric. The displayed user count maps to Sanaei's `limitIp` field, whose enforcement depends on the panel's Fail2ban setup; validate it on the actual panel before selling count-limited plans.

## Products and payment

Services have unlimited, unlimited-duration volume, or timed-volume categories. Admins create fixed plans with limits and per-method prices or custom plans with a pricing rule and selection bounds. The server calculates the quote again during checkout. A trial requires a free plan with both a 1–72 hour duration and a traffic cap.

Zero traffic and zero concurrent IP limits mean unlimited; zero duration means no expiry. The Admin Panel has an explicit one-time free purchase switch for wallet checkout. Timed-volume products still require a positive duration, and trials require a positive traffic cap and duration. Usage is shown only after an actual Sanaei traffic response and carries a sampling timestamp. The Mini App renders a private QR for the first returned connection link and offers a best-effort Hiddify import; other client imports are manual unless their protocol is verified. No subscription URL or online-device count is invented.

The Telegram Mini App offers [Stars for digital services](https://core.telegram.org/bots/payments-stars). A matching `successful_payment` update settles an order; pre-checkout does not. The standalone `/shop/`, accessed with a `/web` one-time code, offers wallet, NOWPayments, and manual payment. NOWPayments requires API key, IPN secret, pay currency, a signed callback, and a fresh server-side payment status. Only `finished` settles the order. Manual payment requires explicit audited admin approval; a receipt image alone is never payment proof. Receipt OCR/AI assistance is optional and **not implemented**. Provider refunds are not automated; terminal external-payment provisioning failures require operator refund action. Wallet refunds are idempotent.

The web shop accepts an optional JPEG/PNG/WebP receipt of up to 3 MB after a manual payment reference is submitted. The image stays in a private volume; the operator downloads it from the Admin Panel and decides approve, reject, or request more information. Business finance entries and manual sales are accounting records, not payment provider events or service provisioning.

## Renewal

In the Telegram Mini App, open **My Services**, select an active, expiring, or expired service, and choose **Renew**. The bot's near-expiry reminder opens the same subscription directly. The server offers compatible enabled plans, quotes the price, and records the chosen limits on the renewal order. A verified Stars payment or atomic wallet debit creates one renewal job; it does not create another subscription. The worker updates the existing Sanaei client and keeps an absolute expiry/traffic target so a retry cannot add the period twice. Remaining time is preserved; expired service time starts when the worker applies the renewal. Timed volume renewals add both plan time and traffic. Unlimited-duration volume plans offer traffic top-up only. Non-expiring unlimited plans have no renewal action.

Check **Payments → Recent renewals** for the order, payment status, worker status, old/new expiry and amount. A terminal ambiguous panel failure opens a refund request for operator review without disabling the original service. Compare the real panel client and Alart subscription before approving; a timeout may have occurred after the panel applied the update. This path and Stars payment/refund behavior require controlled live verification.

## Exact VPS sequence

1. Back up the current deployment. Upload this project and run `./install.sh` on supported Ubuntu. Supply domain, Telegram bot token, Telegram admin ID, and optional OpenAI developer API key. Keep the generated `PANEL_ENCRYPTION_KEY` with encrypted backups.
2. Set BotFather's Web App menu URL to `https://DOMAIN/app/`. Use `/panel` to log into the admin UI. Add a location, then a Sanaei panel. Test connection, review inbounds, allow intended ones, and map products.
3. Create the predefined and custom plans with prices in XTR and, if needed, IRR/USD. Configure and enable Stars. For crypto, enter NOWPayments API key, IPN secret, pay currency, and callback `https://DOMAIN/api/payments/nowpayments/ipn`. For manual payment, enter destination details.
4. Execute controlled live Stars and NOWPayments payments. Check invoice, callback, order amount, single provisioning job, real panel client, delivered link, duplicate update behavior, and alerts. Test manual review, wallet purchase, renewal, trial, time expiry, traffic depletion, and multiple panels.
5. Run `./backup.sh`, verify a restore in a separate environment, and check monitoring and notifications. Only then reassess launch readiness.

Required external values: each Sanaei URL and bearer token; Telegram bot token and admin ID; domain/DNS; actual XTR prices; NOWPayments API key, IPN secret, and pay currency when crypto is enabled; manual destination details when manual payment is enabled. OpenAI needs a separate API key if enabled. ChatGPT Plus does not supply API access.
