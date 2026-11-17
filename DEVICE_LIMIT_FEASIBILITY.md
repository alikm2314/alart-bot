# Device-limit feasibility review

Status: **BLOCKED for reliable device-level connection enforcement.** No HWID implementation or subscription migration has been applied. The deployed Sanaei panel version and customer VPN applications are not available in this workspace, so no live capability test is possible.

## What Alart does today

`alart/sanaei.py` sends `user_limit` as Sanaei `limitIp` on create and renewal. `alart/worker.py` takes that value from the paid order snapshot and stores it on the subscription and provisioning record. The Mini App and admin UI call it “Users”. This is an IP limit and does not distinguish two phones behind one router. Existing subscriptions and their remote clients must retain this behavior unless individually migrated with customer notice.

The current Alart schema and validation require `user_limit > 0`. Despite the requested “keep zero-value semantics”, this baseline does **not** currently support `0 = unlimited` for products; adding it would be a separate, explicit data and pricing change.

Alart returns direct `vless://`, `vmess://`, `trojan://`, and other links from `/panel/api/clients/links/{email}` into its authenticated customer API. Those links contain reusable proxy credentials. They do not require a fresh subscription fetch when a device connects.

## Sanaei capability versus required guarantee

Recent 3x-ui versions have `limitHwid`, a device list, and removal endpoints. The panel obtains `X-HWID` and optional device metadata from a client application's HTTP **subscription request**, hashes the supplied identifier, and limits registration at the subscription server. The gate is implemented in the panel's `EnforceHwidForSubID` service. It is not a physical-device attestation or an Xray inbound connection check. The header can be copied or changed by a client that controls its HTTP request. A proxy link already downloaded can be copied to a fourth device and used with the same credential without another subscription request. Alart's current direct-link delivery bypasses this gate altogether.

The panel feature can be useful as a subscription-sharing deterrent for apps that send a stable `X-HWID`, including some Happ/Incy-style clients. Support for a particular app and version must be verified on the actual deployment. Clients that import a direct link or do not send the header cannot meet the requested guarantee. A device changing networks should keep the same app-generated ID; an app reinstall may generate a new one. Neither property can be guaranteed for all clients from the Alart server.

Official references:

- https://docs.sanaei.dev/docs/reference/api/clients/
- https://docs.sanaei.dev/docs/reference/api/subscription-server/
- https://github.com/MHSanaei/3x-ui/blob/main/internal/web/service/client_hwid.go
- https://docs.sanaei.dev/docs/config/clients/
- https://github.com/XTLS/Xray-core/discussions/4877
- https://xtls.github.io/en/config/inbounds/vless.html
- https://xtls.github.io/en/config/inbounds/vmess.html
- https://xtls.github.io/en/config/inbounds/trojan.html

## What is needed for true device enforcement

Alart would need a selected customer client capable of holding a non-exportable, device-bound private key and proving possession during **each connection**. The server's connection authentication path must validate that proof and bind it to a per-device credential before Xray accepts traffic. This requires a controlled client plus a gateway or protocol/Xray extension with replay protection, device-key rotation, revocation, and atomic registration. Per-device proxy credentials alone limit issued keys but are still copyable, so they are not sufficient for one physical device per slot.

Only after the connection enforcement mechanism is tested should Alart add a `limit_type` snapshot (`ip`, `device`, or `unlimited`), registered-device records, replacement policy, UI, and concurrency tests. Existing rows should backfill as `ip` without modifying remote clients. A new device plan must be unavailable until the selected panel, client, and connection path pass same-IP, changed-IP, copied-link, spoofed-header, reinstall, and simultaneous-first-connection tests.

## Immediate operating decision

Do not sell current “1 User / 2 Users / 3 Users” plans as device-limited plans. They currently map to simultaneous source-IP limits, whose enforcement also depends on Sanaei's Fail2ban setup. Keep current subscriptions unchanged and disclose the IP semantics to customers until a real device-enforcement architecture is chosen and verified.
