#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

readonly config_file=/etc/team-activity-backup/restic.env
readonly lock_file=/run/lock/team-activity-offsite-backup.lock
readonly restore_root=/var/lib/team-activity-backup/verify
readonly restored_dump="$restore_root/var/lib/team-activity-backup/staging/activity_trial.dump"
readonly postgres_container=team-activity-trial-postgres-1
readonly verify_db=activity_backup_restore_verify

exec 9>"$lock_file"
flock -n 9 || {
  echo 'A backup or verification run is already active.' >&2
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
  docker exec "$postgres_container" dropdb \
    -U activity_admin \
    --if-exists \
    --force \
    "$verify_db" >/dev/null 2>&1 || true
  rm -rf -- "$restore_root"
}
trap cleanup EXIT

rm -rf -- "$restore_root"
install -d -m 0700 -- "$restore_root"

restic_cmd check --read-data-subset=5%
restic_cmd restore latest \
  --host team-activity-vps \
  --tag team-activity \
  --include '/var/lib/team-activity-backup/staging/activity_trial.dump' \
  --target "$restore_root"

test -s "$restored_dump"
pg_restore --list "$restored_dump" >/dev/null

docker exec "$postgres_container" dropdb \
  -U activity_admin \
  --if-exists \
  --force \
  "$verify_db"
docker exec "$postgres_container" createdb \
  -U activity_admin \
  -T template0 \
  "$verify_db"
docker exec -i "$postgres_container" pg_restore \
  -U activity_admin \
  -d "$verify_db" \
  --no-owner \
  --no-privileges \
  --exit-on-error <"$restored_dump"
docker exec "$postgres_container" psql \
  -U activity_admin \
  -d "$verify_db" \
  -v ON_ERROR_STOP=1 \
  -Atc "SELECT CASE WHEN to_regnamespace('ticketing') IS NOT NULL AND to_regnamespace('reporting') IS NOT NULL THEN 'RESTORE_OK' ELSE 'RESTORE_INVALID' END;" \
  | grep -Fx RESTORE_OK

echo 'Offsite backup restore verification completed successfully.'
