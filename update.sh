#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
[[ -f .env && -f Caddyfile && -f VERSION ]] || { echo "Installation or VERSION not found" >&2; exit 1; }
[[ $# -le 1 ]] || { echo "Usage: ./update.sh [release.tar.gz]" >&2; exit 1; }
command -v crontab >/dev/null || { echo "cron is required for scheduled backups" >&2; exit 1; }
if ! grep -q '^PANEL_ENCRYPTION_KEY=' .env; then
  panel_key=$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '\n')
  printf '\nPANEL_ENCRYPTION_KEY=%s\n' "$panel_key" >> .env
  chmod 600 .env
fi
if ! grep -q '^BACKUP_ENCRYPTION_KEY=' .env; then
  backup_key=$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '\n')
  printf '\nBACKUP_ENCRYPTION_KEY=%s\n' "$backup_key" >> .env
  chmod 600 .env
  echo "Generated BACKUP_ENCRYPTION_KEY. Save it offline before relying on encrypted backups."
fi
previous=$(cat .deployed-version 2>/dev/null || echo unknown)
if [[ $# -eq 1 ]]; then
  [[ -f $1 ]] || { echo "Release archive not found" >&2; exit 1; }
  command -v python3 >/dev/null || { echo "python3 is required to apply a release archive" >&2; exit 1; }
  chmod +x backup.sh schedule-backups.sh
  ./backup.sh
  python3 -m alart.release apply "$1"
else
  echo "No release archive supplied; using source already staged in this directory. Preserve the previous source separately."
fi
target=$(cat VERSION)
echo "Updating Alart $previous -> $target"
if [[ -d .git ]]; then echo "Source revision: $(git rev-parse --short HEAD)"; fi
docker compose build api worker
chmod +x backup.sh schedule-backups.sh
if [[ $# -eq 0 ]]; then ./backup.sh; fi
if ! docker compose up -d; then
  echo "Update failed. Encrypted backup was created; inspect logs and restore if required." >&2
  exit 1
fi
domain=$(sed -n 's/^DOMAIN=//p' .env)
curl -fsS --retry 15 --retry-delay 2 "https://$domain/api/health" >/dev/null || { echo "Health check failed; use the encrypted backup and previous source snapshot for recovery" >&2; exit 1; }
worker_container=$(docker compose ps -q worker)
[[ -n $worker_container && $(docker inspect -f '{{.State.Running}}' "$worker_container") == true ]] || { echo "Worker is not running after update" >&2; exit 1; }
printf '%s\n' "$target" > .deployed-version
./schedule-backups.sh
echo "Update complete: $target. .env and Docker volumes were preserved."
