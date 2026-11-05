#!/usr/bin/env bash
# Restore the existing trial into a separate database before applying additive DDL.
set -Eeuo pipefail
umask 077
[[ $EUID -eq 0 ]]
project=/home/teamactivity/team-activity-explorer
cd "$project"
compose() {
  docker compose --project-name team-activity-trial --project-directory "$project" \
    --env-file "$project/.env" -f compose.yaml -f deploy/compose.trial.yaml "$@"
}
sql() { compose exec -T postgres psql -X -U activity_admin -d "$1" -v ON_ERROR_STOP=1 -At "${@:2}"; }
testdb=activity_tenants_verify_20261024
backup=/home/teamactivity/migration-trial/tenant-change-20261024
if [[ ${1:-} == prepare ]]; then
  [[ $(sql postgres -c "SELECT count(*) FROM pg_database WHERE datname='$testdb'") == 0 ]]
  mkdir -m 700 "$backup"
  compose exec -T postgres pg_dump -U activity_admin -d activity_trial -Fc > "$backup/before.dump"
  sha256sum "$backup/before.dump" > "$backup/SHA256SUMS"
  compose exec -T postgres createdb -U activity_admin -T template0 "$testdb"
  compose exec -T postgres pg_restore -U activity_admin -d "$testdb" --no-owner --no-acl --exit-on-error --single-transaction < "$backup/before.dump"
  sed -n '/^GRANT USAGE ON SCHEMA/,/^COMMIT;/p' init/10-schema.sql | sql "$testdb" > "$backup/restore-grants.txt"
  sql "$testdb" < init/20-tenants.sql > "$backup/migration-verify.txt"
  sql "$testdb" < init/20-tenants.sql >> "$backup/migration-verify.txt"
  echo 'Restore and repeat migration verified in separate database; trial not migrated yet.'
elif [[ ${1:-} == apply ]]; then
  [[ -f "$backup/VERIFIED" && -s "$backup/before.dump" ]]
  fingerprint="SELECT md5(COALESCE(jsonb_agg(to_jsonb(t)-'tenant_id' ORDER BY task_id)::text,'')) FROM work.tasks t;
    SELECT md5(COALESCE(jsonb_agg(to_jsonb(p)-'tenant_id' ORDER BY proposal_id)::text,'')) FROM work.proposals p;
    SELECT md5(COALESCE(jsonb_agg(to_jsonb(s)-'tenant_id'-'account_id' ORDER BY source_id)::text,'')) FROM raw.sources s;
    SELECT count(*) FROM raw.messages; SELECT count(*) FROM raw.message_revisions;
    SELECT count(*) FROM work.events; SELECT count(*) FROM work.evidence; SELECT count(*) FROM ops.review_log;"
  sql activity_trial -c "$fingerprint" > "$backup/before-fingerprint.txt"
  sql activity_trial < init/20-tenants.sql > "$backup/migration-applied.txt"
  sql activity_trial -c "$fingerprint" > "$backup/after-fingerprint.txt"
  cmp "$backup/before-fingerprint.txt" "$backup/after-fingerprint.txt"
  echo 'Trial migration applied; existing task/proposal/source contents and evidence counts preserved.'
else
  echo 'Usage: check-tenant-migration.sh prepare|apply' >&2
  exit 2
fi
