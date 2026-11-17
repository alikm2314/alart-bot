#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
[[ $# -eq 1 && -f $1 ]] || { echo "Usage: ./restore.sh backups/TIMESTAMP.dump.enc" >&2; exit 1; }
[[ -f .env ]] || { echo "Restore the matching .env before restoring data" >&2; exit 1; }
echo "This will replace the current Alart database. Type RESTORE to continue:"
read -r answer
[[ $answer == RESTORE ]] || exit 1
umask 077
staged=$(mktemp "${TMPDIR:-/tmp}/alart-restore.XXXXXXXX.dump")
trap 'rm -f -- "$staged"' EXIT
case $1 in
  *.dump.enc) docker compose run --rm --no-deps -T api python -m alart.backup_crypto decrypt < "$1" > "$staged" ;;
  *.dump) echo "Warning: restoring a legacy unencrypted dump" >&2; cp -- "$1" "$staged" ;;
  *) echo "Unsupported backup format" >&2; exit 1 ;;
esac
[[ -s $staged ]] || { echo "Decrypted dump is empty" >&2; exit 1; }
docker compose stop api worker
if ! docker compose exec -T db pg_restore -U alart -d alart --clean --if-exists --no-owner < "$staged"; then
  echo "Restore failed. Database may be partial; keep services stopped and investigate." >&2
  exit 1
fi
docker compose up -d api worker
echo "Database restored. Verify API health and provider connectivity."
