#!/usr/bin/env bash
set -euo pipefail
umask 077

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Run with sudo: sudo bash $0" >&2
  exit 1
fi

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
config_file="${1:-/home/teamactivity/.config/integration-task-manager/database.env}"
migration_file="$project_root/init/30-ticketing.sql"
backup_root=/home/teamactivity/migration-trial/ticketing

if [[ ! -s "$config_file" ]]; then
  echo "Missing ticketing database configuration: $config_file" >&2
  exit 1
fi
# shellcheck disable=SC1090
source "$config_file"
: "${TICKETING_DB_NAME:?required}"
: "${TICKETING_DB_USER:?required}"
: "${TICKETING_DB_PASSWORD:?required}"

if [[ "$TICKETING_DB_NAME" != "activity_trial" ]]; then
  echo "Refusing unexpected production database: $TICKETING_DB_NAME" >&2
  exit 1
fi
if [[ ! "$TICKETING_DB_USER" =~ ^[a-z_][a-z0-9_]{1,31}$ ]]; then
  echo "Invalid TICKETING_DB_USER" >&2
  exit 1
fi
test -s "$migration_file"

compose=(docker compose -p team-activity-trial --project-directory "$project_root"
  --env-file "$project_root/.env" -f "$project_root/compose.yaml"
  -f "$project_root/deploy/compose.trial.yaml")
postgres_container="$("${compose[@]}" ps -q postgres)"
test -n "$postgres_container"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_dir="$backup_root/$timestamp"
backup_file="$backup_dir/activity_trial-before-ticketing.dump"
scratch_db="ticketing_verify_${timestamp//[^0-9]/}"
mkdir -p "$backup_dir"
chmod 700 "$backup_dir"

cleanup() {
  docker exec "$postgres_container" dropdb -U activity_admin --if-exists "$scratch_db" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker exec "$postgres_container" pg_dump \
  -U activity_admin -d "$TICKETING_DB_NAME" --format=custom > "$backup_file"
test -s "$backup_file"
sha256sum "$backup_file" > "$backup_file.sha256"

docker exec "$postgres_container" createdb -U activity_admin "$scratch_db"
docker exec -i "$postgres_container" pg_restore \
  -U activity_admin -d "$scratch_db" --no-owner --no-privileges < "$backup_file"
docker exec -i "$postgres_container" psql \
  -U activity_admin -d "$scratch_db" --set ON_ERROR_STOP=1 < "$migration_file"

docker exec -i "$postgres_container" psql \
  -U activity_admin -d "$scratch_db" --set ON_ERROR_STOP=1 <<'SQL'
BEGIN;
SELECT ticketing.register_member('kalisto',990000000001,'synthetic_admin','Synthetic Admin','admin','2026-10-29T09:00:00Z');
SELECT ticketing.register_member('kalisto',990000000002,'synthetic_tech','Synthetic Tech','technician','2026-10-29T09:00:00Z');
SELECT ticketing.register_member('kalisto',990000000003,'synthetic_client','Synthetic Client','client','2026-10-29T09:00:00Z');
SELECT ticketing.register_member('mephisto',990000000001,'synthetic_admin','Synthetic Admin','admin','2026-10-29T09:00:00Z');
SELECT ticketing.create_ticket('kalisto',990000000003,990000000002,'Synthetic migration check','2026-10-29T09:01:00Z','migration-check:ticket');
SELECT ticketing.add_message('kalisto',currval('ticketing.tickets_ticket_id_seq'),990000000003,'public','Synthetic question','2026-10-29T09:02:00Z','migration-check:message');
SELECT ticketing.change_status('kalisto',currval('ticketing.tickets_ticket_id_seq'),990000000002,'completed','2026-10-29T09:03:00Z','migration-check:completed');
SELECT ticketing.sync_configured_roles('kalisto',ARRAY[990000000001]::bigint[],ARRAY[]::bigint[]);
DO $$
BEGIN
  IF (SELECT count(*) FROM reporting.ticket_facts WHERE description='Synthetic migration check') <> 1 THEN
    RAISE EXCEPTION 'ticketing_reporting_check_failed';
  END IF;
  IF (SELECT count(*) FROM ticketing.memberships m JOIN ticketing.telegram_users u USING(user_id)
      WHERE u.telegram_user_id=990000000001) <> 2 THEN
    RAISE EXCEPTION 'multi_profile_membership_check_failed';
  END IF;
  IF (SELECT m.role FROM ticketing.memberships m JOIN ticketing.telegram_users u USING(user_id)
      WHERE m.profile_key='kalisto' AND u.telegram_user_id=990000000002) <> 'client' THEN
    RAISE EXCEPTION 'configured_role_sync_check_failed';
  END IF;
  IF (SELECT actor_role FROM ticketing.ticket_events
      WHERE profile_key='kalisto' AND idempotency_key='migration-check:completed') <> 'technician' THEN
    RAISE EXCEPTION 'historical_actor_role_check_failed';
  END IF;
END $$;
ROLLBACK;
SQL

docker exec -i "$postgres_container" psql \
  -U activity_admin -d "$TICKETING_DB_NAME" --set ON_ERROR_STOP=1 < "$migration_file"

docker exec -i \
  -e TICKETING_WRITER_PASSWORD="$TICKETING_DB_PASSWORD" \
  "$postgres_container" psql -U activity_admin -d "$TICKETING_DB_NAME" \
  --set ON_ERROR_STOP=1 --set ticketing_user="$TICKETING_DB_USER" \
  --set database_name="$TICKETING_DB_NAME" <<'SQL'
\getenv ticketing_password TICKETING_WRITER_PASSWORD
SELECT format(
  'CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION',
  :'ticketing_user', :'ticketing_password'
) WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=:'ticketing_user') \gexec
SELECT format('ALTER ROLE %I PASSWORD %L', :'ticketing_user', :'ticketing_password') \gexec
SELECT format('GRANT ticketing_writer TO %I', :'ticketing_user') \gexec
SELECT format('ALTER ROLE %I SET statement_timeout = %L', :'ticketing_user', '30s') \gexec
SELECT format('ALTER ROLE %I SET idle_in_transaction_session_timeout = %L', :'ticketing_user', '30s') \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', :'database_name', :'ticketing_user') \gexec
SQL

docker exec -e PGPASSWORD="$TICKETING_DB_PASSWORD" "$postgres_container" \
  psql -h 127.0.0.1 -U "$TICKETING_DB_USER" -d "$TICKETING_DB_NAME" \
  --set ON_ERROR_STOP=1 --tuples-only --no-align \
  --command "SELECT count(*) FROM reporting.ticket_facts;" >/dev/null

docker exec "$postgres_container" psql -U activity_admin -d "$TICKETING_DB_NAME" \
  --set ON_ERROR_STOP=1 --tuples-only --no-align \
  --command "SELECT count(*) FROM information_schema.tables WHERE table_schema='ticketing';"

cleanup
trap - EXIT
echo "TICKETING_MIGRATION_READY backup=$backup_file"
