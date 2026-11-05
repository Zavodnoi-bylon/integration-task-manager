#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
[[ $EUID -eq 0 ]]
project=/home/teamactivity/team-activity-explorer
cd "$project"
compose() { docker compose --project-name team-activity-trial --project-directory "$project" --env-file "$project/.env" -f compose.yaml -f deploy/compose.trial.yaml "$@"; }
sql() { compose exec -T postgres psql -X -U activity_admin -d "$1" -v ON_ERROR_STOP=1 -At "${@:2}"; }
target=activity_rebuild_20261025
[[ $(sql postgres -c "SELECT count(*) FROM pg_database WHERE datname='$target'") == 0 ]]
backup=/home/teamactivity/migration-trial/before-clean-report-20261025
mkdir -m 700 "$backup"
compose exec -T postgres pg_dump -U activity_admin -d activity_trial -Fc > "$backup/activity.dump"
sha256sum "$backup/activity.dump" > "$backup/SHA256SUMS"
compose exec -T postgres createdb -U activity_admin -T template0 "$target"
compose exec -T postgres pg_restore -U activity_admin -d "$target" --no-owner --no-acl --exit-on-error --single-transaction < "$backup/activity.dump"
sed -n '/^GRANT USAGE ON SCHEMA/,/^COMMIT;/p' init/10-schema.sql | sql "$target" > "$backup/restore-grants.txt"
sql "$target" < init/20-tenants.sql > "$backup/tenant-grants.txt"
sql "$target" -c "BEGIN; TRUNCATE work.evidence,work.events,work.tasks,ops.review_log,work.proposals;
DELETE FROM ops.import_runs WHERE kind='task-proposals'; COMMIT;"
sql "$target" -c "SELECT 'tasks',count(*) FROM work.tasks UNION ALL SELECT 'events',count(*) FROM work.events UNION ALL SELECT 'proposals',count(*) FROM work.proposals UNION ALL SELECT 'raw_messages',count(*) FROM raw.messages;"
echo 'Clean derived layer prepared in isolated rebuild DB. Serving DB unchanged; full backup retained.'
