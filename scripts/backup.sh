#!/usr/bin/env bash
set -euo pipefail
umask 077
activity_project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
activity_backup_dir="${1:?Usage: bash scripts/backup.sh /absolute/backup/directory}"
if [[ "$activity_backup_dir" != /* ]]; then
  echo 'Use an absolute backup directory.' >&2
  exit 2
fi
mkdir -p -- "$activity_backup_dir"
chmod 700 -- "$activity_backup_dir"
activity_partial="$(mktemp "$activity_backup_dir/.activity-partial.XXXXXXXX")"
trap 'rm -f -- "$activity_partial"' EXIT
sudo docker compose --project-directory "$activity_project_dir" exec -T postgres \
  pg_dump -U activity_admin -d activity --format=custom > "$activity_partial"
test -s "$activity_partial"
activity_destination="$activity_backup_dir/activity-$(date -u +%Y%m%dT%H%M%S)-$(basename "$activity_partial").dump"
mv -- "$activity_partial" "$activity_destination"
sha256sum -- "$activity_destination" > "$activity_destination.sha256"
echo "Backup complete: $activity_destination"

