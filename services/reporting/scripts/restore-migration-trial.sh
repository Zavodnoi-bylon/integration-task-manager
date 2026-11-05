#!/usr/bin/env bash
# Restore only into a NEW trial database on the NEW server. Never replaces a DB.
set -Eeuo pipefail
umask 077
[[ $EUID -eq 0 ]] || { echo 'Run as administrator.' >&2; exit 1; }
project=/home/teamactivity/team-activity-explorer
snapshot=${1:?Pass absolute trial snapshot directory}
[[ "$snapshot" == /home/teamactivity/migration-trial/snapshot-* && -d "$snapshot" && ! -L "$snapshot" ]]
[[ -f "$snapshot/READY" ]]
cd "$snapshot"
sha256sum -c SHA256SUMS
cd "$project"
compose() {
  docker compose --project-name team-activity-trial --project-directory "$project" \
    --env-file "$project/.env" --file "$project/compose.yaml" \
    --file "$project/deploy/compose.trial.yaml" "$@"
}
sql() { compose exec -T postgres psql -U activity_admin -d "$1" -v ON_ERROR_STOP=1 -At "${@:2}"; }
[[ $(sql postgres -c "SELECT count(*) FROM pg_database WHERE datname='activity_trial';") == 0 ]] \
  || { echo 'Trial database exists; refusing to replace it.' >&2; exit 1; }
compose exec -T postgres createdb -U activity_admin -T template0 activity_trial
compose exec -T postgres pg_restore -U activity_admin -d activity_trial \
  --no-owner --no-acl --exit-on-error --single-transaction < "$snapshot/activity.dump"
{
  printf '%s\n' 'BEGIN;' 'REVOKE CREATE ON SCHEMA public FROM PUBLIC;' \
    'REVOKE ALL ON DATABASE activity_trial FROM PUBLIC;' \
    'GRANT CONNECT ON DATABASE activity_trial TO activity_reader, activity_writer;'
  sed -n '/^GRANT USAGE ON SCHEMA/,/^COMMIT;/p' init/10-schema.sql
} | sql activity_trial > "$snapshot/permissions-applied.txt"
for table in ops.dataset ops.import_runs ops.review_log raw.sources raw.messages \
    raw.message_revisions work.proposals work.tasks work.events work.evidence; do
  printf '%s|' "$table"
  sql activity_trial -c "SELECT count(*) FROM $table;"
done > "$snapshot/counts-restored.txt"
cmp "$snapshot/counts-before.txt" "$snapshot/counts-restored.txt"
[[ $(sql activity_trial -c "SELECT count(*) FROM pg_constraint WHERE connamespace IN ('raw'::regnamespace,'work'::regnamespace,'ops'::regnamespace) AND NOT convalidated;") == 0 ]]
[[ $(sql activity_trial -c "SELECT has_table_privilege('activity_reader','work.tasks','SELECT'),has_table_privilege('activity_reader','work.tasks','UPDATE'),has_schema_privilege('activity_reader','work','CREATE'),has_table_privilege('activity_writer','raw.message_revisions','UPDATE'),has_table_privilege('activity_writer','ops.review_log','UPDATE');") == 't|f|f|f|f' ]]
compose up -d --wait web
.venv/bin/python - "$snapshot" <<'PY'
import json
import sys
import urllib.request
from pathlib import Path
snapshot = Path(sys.argv[1])
def get(route):
    with urllib.request.urlopen('http://127.0.0.1:8481' + route, timeout=15) as response:
        return json.load(response)
assert get('/api/health') == {'ok': 1, 'mode': 'postgres'}
assert get('/api/meta')['demo'] is False
report = get('/api/report?from=2026-10-01&to=2026-10-23')
expected = json.loads((snapshot / 'report.json').read_text())
assert report == expected, 'Restored API report differs from source trial baseline'
with urllib.request.urlopen('http://127.0.0.1:8481/', timeout=15) as response:
    assert response.status == 200
(snapshot / 'report-restored.json').write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
(snapshot / 'RESTORE_VERIFIED').touch()
print('PASS: restored table counts, constraints, privileges, complete API report and UI.')
print(json.dumps(report['summary'], ensure_ascii=False))
PY
chown -R teamactivity:teamactivity "$snapshot"
compose ps
