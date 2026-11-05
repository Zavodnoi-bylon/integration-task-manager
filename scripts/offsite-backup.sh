#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

readonly config_file=/etc/team-activity-backup/restic.env
readonly lock_file=/run/lock/team-activity-offsite-backup.lock
readonly staging_dir=/var/lib/team-activity-backup/staging
readonly dump_file="$staging_dir/activity_trial.dump"
readonly checksum_file="$dump_file.sha256"
readonly globals_file="$staging_dir/postgres-globals.sql"
readonly postgres_container=team-activity-trial-postgres-1

exec 9>"$lock_file"
flock -n 9 || {
  echo 'Another team activity backup is already running.' >&2
  exit 75
}

if [[ ! -r "$config_file" ]]; then
  echo "Missing backup configuration: $config_file" >&2
  exit 78
fi

# shellcheck disable=SC1091
source "$config_file"

: "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY is required}"
: "${RESTIC_PASSWORD_FILE:?RESTIC_PASSWORD_FILE is required}"
: "${RESTIC_SFTP_COMMAND:?RESTIC_SFTP_COMMAND is required}"

restic_cmd() {
  restic -o "sftp.command=$RESTIC_SFTP_COMMAND" "$@"
}

cleanup() {
  rm -f -- "$dump_file" "$checksum_file" "$globals_file"
}
trap cleanup EXIT

install -d -m 0700 -- "$staging_dir"
rm -f -- "$dump_file" "$checksum_file" "$globals_file"

docker inspect "$postgres_container" >/dev/null
docker exec "$postgres_container" pg_dump \
  -U activity_admin \
  -d activity_trial \
  --format=custom \
  --no-owner \
  --no-privileges >"$dump_file"

test -s "$dump_file"
pg_restore --list "$dump_file" >/dev/null
sha256sum "$dump_file" >"$checksum_file"
docker exec "$postgres_container" pg_dumpall \
  -U activity_admin \
  --globals-only >"$globals_file"
test -s "$globals_file"

backup_paths=(
  "$staging_dir"
  /home/teamactivity/.config
  /home/teamactivity/ui-state
  /home/teamactivity/team-activity-ingest
  /home/teamactivity/maintenance
  /home/teamactivity/migration-trial
  /home/teamactivity/integration-task-manager/runtime.env
  /home/teamactivity/dest-bot-miniapp/runtime.env
  /home/teamactivity/team-activity-explorer/.env
  /home/teamactivity/team-activity-explorer/.env.client
  /etc/aio-domain-monitor.env
  /etc/team-activity-lead
  /var/lib/aio-domain-monitor
  /etc/systemd/system/team-activity-collect.service
  /etc/systemd/system/team-activity-collect.timer
)

existing_paths=()
for path in "${backup_paths[@]}"; do
  if [[ -e "$path" ]]; then
    existing_paths+=("$path")
  fi
done

restic_cmd backup \
  --host team-activity-vps \
  --tag team-activity \
  --exclude-caches \
  --exclude '/home/teamactivity/team-activity-ingest/.venv' \
  --exclude '/home/teamactivity/team-activity-ingest/**/__pycache__' \
  "${existing_paths[@]}"

restic_cmd forget \
  --host team-activity-vps \
  --tag team-activity \
  --keep-daily 7 \
  --keep-weekly 5 \
  --keep-monthly 12 \
  --prune

restic_cmd snapshots --host team-activity-vps --tag team-activity --latest 1
