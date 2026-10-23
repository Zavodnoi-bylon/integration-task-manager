#!/usr/bin/env bash
# Read-only source export for a trial restore; never stops services or collection.
# Run interactively with sudo on the OLD server only.
set -Eeuo pipefail
umask 077
[[ $EUID -eq 0 ]] || { echo 'Run with sudo.' >&2; exit 1; }
project=/home/vaultadmin/team-activity-explorer
parent=/home/vaultadmin/team-activity-migration-trial
[[ -d "$project" && ! -L "$project" && ! -L "$parent" ]]
install -d -m 700 -o vaultadmin -g vaultadmin "$parent"
output=$(mktemp -d "$parent/snapshot-XXXXXXXX")
trap 'chown -R vaultadmin:vaultadmin "$output"' EXIT
compose() {
  docker compose --project-name team-activity-explorer \
    --project-directory "$project" --env-file "$project/.env" \
    --file "$project/compose.yaml" "$@"
}
counts() {
  for table in ops.dataset ops.import_runs ops.review_log raw.sources raw.messages \
      raw.message_revisions work.proposals work.tasks work.events work.evidence; do
    printf '%s|' "$table"
    compose exec -T postgres psql -U activity_admin -d activity -v ON_ERROR_STOP=1 \
      -At -c "SELECT count(*) FROM $table;"
  done
}
date -u +%FT%TZ > "$output/started-at.txt"
counts > "$output/counts-before.txt"
compose exec -T postgres pg_dump -U activity_admin -d activity --format=custom \
  > "$output/activity.dump"
test -s "$output/activity.dump"
counts > "$output/counts-after.txt"
# This is a trial export, not a coordinated final cutover snapshot.
# Counts bracket the consistent pg_dump snapshot; refuse observed source changes.
cmp "$output/counts-before.txt" "$output/counts-after.txt"
curl --fail --silent --show-error http://127.0.0.1:8480/api/health > "$output/health.json"
curl --fail --silent --show-error \
  'http://127.0.0.1:8480/api/report?from=2026-10-01&to=2026-10-23' > "$output/report.json"
curl --fail --silent --show-error http://127.0.0.1:8480/api/meta > "$output/meta.json"
date -u +%FT%TZ > "$output/finished-at.txt"
(cd "$output"; sha256sum activity.dump counts-before.txt counts-after.txt health.json report.json meta.json > SHA256SUMS)
touch "$output/READY"
chown -R vaultadmin:vaultadmin "$output"
echo "Trial export ready: $output"
echo 'Old services remain running. This is not a final migration snapshot.'
