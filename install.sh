#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
fail(){ echo "Installation stopped: $*" >&2; exit 1; }
[[ $(uname -s) == Linux ]] || fail "Linux is required"
[[ $(uname -m) == x86_64 || $(uname -m) == aarch64 ]] || fail "x86_64 or aarch64 is required"
[[ -f /etc/os-release ]] || fail "Cannot detect Linux distribution"
. /etc/os-release
[[ ${ID:-} == ubuntu ]] || fail "Ubuntu is required"
case "${VERSION_ID:-}" in 22.04|24.04) ;; *) fail "Ubuntu 22.04 or 24.04 is required";; esac
for command in docker curl dig ss openssl awk crontab flock; do command -v "$command" >/dev/null || fail "$command is required"; done
docker compose version >/dev/null || fail "Docker Compose plugin is required"
docker info >/dev/null 2>&1 || fail "Docker daemon is unavailable or this user lacks Docker access"
[[ $(nproc) -ge 1 ]] || fail "At least 1 CPU core is required"
[[ $(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo) -ge 1750 ]] || fail "At least 1.75 GiB RAM is required"
[[ $(df -Pm . | awk 'NR==2{print $4}') -ge 5120 ]] || fail "At least 5 GiB free disk space is required"
for port in 80 443; do if ss -ltn "( sport = :$port )" | tail -n +2 | grep -q .; then fail "Port $port is already in use"; fi; done
curl -sS --max-time 8 -o /dev/null https://api.telegram.org || fail "Cannot reach Telegram API"
ipv4=$(curl -4fsS --max-time 8 https://api.ipify.org) || fail "Cannot detect public IPv4"
[[ $ipv4 =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "Invalid public IPv4 detected"
read -rp "Enter your domain: " domain
[[ $domain =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*\.[a-zA-Z]{2,}$ ]] || fail "Invalid domain"
mapfile -t records < <(dig +short A "$domain" | sort -u)
[[ ${#records[@]} -eq 1 && ${records[0]} == "$ipv4" ]] || fail "Domain A records must point only to $ipv4. Found: ${records[*]:-none}"
ipv6_records=$(dig +short AAAA "$domain")
if [[ -n $ipv6_records ]]; then
  local_ipv6=$(curl -6fsS --max-time 8 https://api64.ipify.org 2>/dev/null || true)
  [[ -n $local_ipv6 && $ipv6_records == "$local_ipv6" ]] || fail "Domain AAAA record does not match this VPS IPv6"
fi
[[ ! -e .env ]] || fail ".env exists; use ./update.sh for an existing installation"
read -rsp "Telegram bot token: " bot_token; echo
[[ $bot_token =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]] || fail "Invalid bot token format"
curl -fsS --max-time 8 "https://api.telegram.org/bot$bot_token/getMe" | grep -q '"ok":true' || fail "Telegram rejected the bot token"
read -rp "Telegram admin numeric ID: " admin_id
[[ $admin_id =~ ^[0-9]+$ ]] || fail "Admin ID must be numeric"
read -rsp "OpenAI API key (optional, press Enter to skip): " openai_key; echo
read -rp "OpenAI model (optional, press Enter for default): " openai_model
db_password=$(openssl rand -hex 32)
session_secret=$(openssl rand -hex 48)
panel_key=$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '\n')
backup_key=$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '\n')
umask 077
cat > .env <<EOF
DOMAIN=$domain
BOT_TOKEN=$bot_token
ADMIN_TELEGRAM_ID=$admin_id
POSTGRES_PASSWORD=$db_password
SESSION_SECRET=$session_secret
PANEL_ENCRYPTION_KEY=$panel_key
BACKUP_ENCRYPTION_KEY=$backup_key
PROVISIONING_BASE_URL=
PROVISIONING_TOKEN=
OPENAI_API_KEY=$openai_key
OPENAI_MODEL=$openai_model
NOWPAYMENTS_API_KEY=
NOWPAYMENTS_IPN_SECRET=
OPS_CHAT_ID=
OPS_TOPIC_PURCHASES=
OPS_TOPIC_PAYMENTS=
OPS_TOPIC_WALLET=
OPS_TOPIC_REFERRAL=
OPS_TOPIC_USERS=
OPS_TOPIC_PROVISIONING=
OPS_TOPIC_ERRORS=
OPS_TOPIC_SECURITY=
OPS_TOPIC_BACKUP=
OPS_TOPIC_SYSTEM=
EOF
sed "s/DOMAIN_PLACEHOLDER/$domain/" Caddyfile.template > Caddyfile
chmod 600 .env Caddyfile
docker compose up -d --build || fail "Container startup failed; inspect docker compose logs"
for attempt in $(seq 1 40); do
if curl -fsS --max-time 5 "https://$domain/api/health" >/dev/null; then
    cp VERSION .deployed-version
    chmod +x backup.sh schedule-backups.sh
    ./schedule-backups.sh || fail "Could not schedule backups"
    ./backup.sh || fail "Could not create the initial encrypted backup"
    echo "Installation completed successfully."
    echo "Mini App: https://$domain/app/"
    echo "Admin Panel: https://$domain/panel/"
    echo "API: https://$domain/api/"
    echo "Configure real servers and plans before accepting purchases."
    exit 0
  fi
  sleep 5
done
fail "HTTPS health check failed. Inspect docker compose logs; configuration remains in .env"
