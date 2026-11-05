#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Run as root: sudo bash deploy/apply-aio-ftd-migration.sh" >&2
  exit 1
fi

source_dir=${SOURCE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
sql_file="$source_dir/deploy/aio-ftd-alerts.sql"
database_env=${DATABASE_ENV_FILE:-/home/teamactivity/.config/integration-task-manager/database.env}

if [[ ! -r "$database_env" ]]; then
  echo "Database configuration was not found: $database_env" >&2
  exit 1
fi

read_env_value() {
  local key=$1
  awk -F= -v key="$key" '
    $1 == key {
      sub(/^[^=]*=/, "")
      sub(/\r$/, "")
      print
      exit
    }
  ' "$database_env"
}

target_database=$(read_env_value TICKETING_DB_NAME)
application_role=$(read_env_value TICKETING_DB_USER)
if [[ -z "$target_database" || -z "$application_role" ]]; then
  echo "TICKETING_DB_NAME and TICKETING_DB_USER are required." >&2
  exit 1
fi

postgres_id=$(docker ps --filter label=com.docker.compose.project=team-activity-trial \
  --filter label=com.docker.compose.service=postgres --format '{{.ID}}' | head -n 1)
if [[ -z "$postgres_id" ]]; then
  echo "PostgreSQL container for team-activity-trial was not found." >&2
  exit 1
fi

docker exec -i \
  -e TARGET_DATABASE="$target_database" \
  -e APPLICATION_ROLE="$application_role" \
  "$postgres_id" sh -lc \
  'psql -v ON_ERROR_STOP=1 -v app_role="$APPLICATION_ROLE" \
    -U "$POSTGRES_USER" -d "$TARGET_DATABASE"' <"$sql_file"

docker exec -e TARGET_DATABASE="$target_database" "$postgres_id" sh -lc \
  'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$TARGET_DATABASE" -Atc \
  "SELECT to_regclass('\''aio_alerts.monitor_state'\''),to_regclass('\''aio_alerts.alert_events'\'')"'

echo "AIO_FTD_SCHEMA_READY"
