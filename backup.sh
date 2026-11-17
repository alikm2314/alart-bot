#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
[[ -f .env && -f Caddyfile ]] || { echo "Installation configuration is missing" >&2; exit 1; }
grep -Eq '^BACKUP_ENCRYPTION_KEY=[A-Za-z0-9_-]{43}=?$' .env || { echo "BACKUP_ENCRYPTION_KEY is missing; refusing an unencrypted backup" >&2; exit 1; }
umask 077
mkdir -p backups
command -v flock >/dev/null || { echo "flock is required for safe backup scheduling" >&2; exit 1; }
exec 9>backups/.backup.lock
flock -n 9 || { echo "Another backup is running" >&2; exit 1; }
stamp=$(date -u +%Y%m%dT%H%M%SZ)
while [[ -e backups/$stamp.dump.enc ]]; do sleep 1; stamp=$(date -u +%Y%m%dT%H%M%SZ); done
dump="backups/$stamp.dump.enc"
config="backups/$stamp-config.tar.gz.enc"
source_archive="backups/$stamp-source.tar.gz.enc"
private_archive="backups/$stamp-private.tar.gz.enc"
trap 'rm -f -- "$dump.tmp" "$config.tmp" "$source_archive.tmp" "$private_archive.tmp"' EXIT
docker compose exec -T db pg_dump -U alart -Fc alart | docker compose run --rm --no-deps -T api python -m alart.backup_crypto encrypt > "$dump.tmp"
tar -czf - .env Caddyfile VERSION | docker compose run --rm --no-deps -T api python -m alart.backup_crypto encrypt > "$config.tmp"
tar -czf - alart web db ./*.sh requirements.txt Dockerfile compose.yaml Caddyfile.template VERSION | docker compose run --rm --no-deps -T api python -m alart.backup_crypto encrypt > "$source_archive.tmp"
docker compose run --rm --no-deps -T api python -m alart.private_archive pack | docker compose run --rm --no-deps -T api python -m alart.backup_crypto encrypt > "$private_archive.tmp"
mv -- "$dump.tmp" "$dump"
mv -- "$config.tmp" "$config"
mv -- "$source_archive.tmp" "$source_archive"
mv -- "$private_archive.tmp" "$private_archive"
shopt -s nullglob
archives=(backups/*.dump.enc)
while (( ${#archives[@]} > 14 )); do
  oldest=${archives[0]}
  base=${oldest#backups/}
  base=${base%.dump.enc}
  [[ $base =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || { echo "Unexpected backup filename; retention stopped" >&2; exit 1; }
  rm -f -- "$oldest" "backups/$base-config.tar.gz.enc" "backups/$base-source.tar.gz.enc" "backups/$base-private.tar.gz.enc"
  archives=("${archives[@]:1}")
done
echo "Encrypted backup created: $dump, $config, $source_archive, and $private_archive"
# Backup archives are deliberately root-only.  Use a short-lived, read-only
# sender process rather than weakening permissions on the archive or API.
docker compose run --rm --no-deps --user 0:0 -T api python -m alart.ops_topics backup "/srv/backups/${stamp}.dump.enc" >/dev/null || echo "Backup file could not be sent to Telegram" >&2
echo "Keep BACKUP_ENCRYPTION_KEY offline; test restores regularly."
