#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]]
project=/home/teamactivity/team-activity-explorer
cd "$project"
compose() { docker compose --project-name team-activity-trial --project-directory "$project" --env-file "$project/.env" -f compose.yaml -f deploy/compose.trial.yaml "$@"; }
sql() { compose exec -T postgres psql -X -U activity_admin -d "$1" -v ON_ERROR_STOP=1 -At "${@:2}"; }
[[ -s state/deploy/clean-report-verification.json ]]
[[ $(sql postgres -c "SELECT count(*) FROM pg_database WHERE datname='activity_archive_20261025'") == 0 ]]
[[ $(sql activity_rebuild_20261025 -c "SELECT count(*) FROM work.evidence WHERE message_key LIKE 'telegram-desktop:8855708533:%' OR message_key LIKE 'telegram-desktop:8011364022:%'") == 0 ]]
fingerprint="SELECT md5(string_agg(message_key||':'||revision_hash,',' ORDER BY message_key,revision_hash)) FROM raw.message_revisions;"
[[ $(sql activity_trial -c "$fingerprint") == $(sql activity_rebuild_20261025 -c "$fingerprint") ]]
compose stop web
sql postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname IN ('activity_trial','activity_rebuild_20261025') AND pid<>pg_backend_pid();" > /dev/null
sql postgres -c 'ALTER DATABASE activity_trial RENAME TO activity_archive_20261025;'
if ! sql postgres -c 'ALTER DATABASE activity_rebuild_20261025 RENAME TO activity_trial;'; then
  sql postgres -c 'ALTER DATABASE activity_archive_20261025 RENAME TO activity_trial;'
  compose up -d --no-deps web
  exit 1
fi
compose up -d --no-deps web
echo 'Clean report activated; old full database preserved as activity_archive_20261025.'
