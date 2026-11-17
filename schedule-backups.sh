#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
command -v crontab >/dev/null || { echo "Install the cron package before scheduling backups" >&2; exit 1; }
workspace=$(pwd)
[[ $workspace != *"'"* ]] || { echo "Workspace path contains an unsupported quote" >&2; exit 1; }
mkdir -p backups
chmod 700 backups
existing=$(crontab -l 2>/dev/null || true)
cleaned=$(printf '%s\n' "$existing" | sed '/# alart-backup$/d')
{
  printf '%s\n' "$cleaned"
  printf "0 * * * * cd '%s' && ./backup.sh >> backups/backup.log 2>&1 # alart-backup\n" "$workspace"
} | crontab -
echo "Encrypted backups scheduled every hour (14 versions retained)."
