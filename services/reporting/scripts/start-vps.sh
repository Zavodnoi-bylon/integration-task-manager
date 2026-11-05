#!/usr/bin/env bash
# Run manually by vaultadmin with sudo. Only this NEW Compose project is managed.
set -Eeuo pipefail
umask 077
activity_project=/home/vaultadmin/team-activity-explorer
activity_name=team-activity-explorer
activity_release=aabe799ea8675e220ceaaa4ed0e0435413f035cd619815e9def700980ace335c

[[ ${EUID} -eq 0 ]] || { echo 'Run this script with sudo.' >&2; exit 1; }
[[ -d "$activity_project" && ! -L "$activity_project" ]]
cd -- "$activity_project"
[[ -f compose.yaml && -f .env ]]
# Pin the project and file so COMPOSE_* environment settings cannot select
# another project or an automatically discovered override file.
activity_compose() {
  docker compose --project-name "$activity_name" \
    --project-directory "$activity_project" \
    --env-file "$activity_project/.env" \
    --file "$activity_project/compose.yaml" "$@"
}
activity_compose version
activity_compose config --quiet

# Refuse pre-existing Docker resources on the first installation.
# A marker permits retrying our own interrupted build; it contains no secret.
if [[ -e .bootstrap-owner ]]; then
  [[ -f .bootstrap-owner && ! -L .bootstrap-owner ]]
  [[ $(< .bootstrap-owner) == "$activity_release" ]] || {
    echo 'A different bootstrap already owns this directory. Stop and inspect it.' >&2
    exit 1
  }
else
  activity_containers=$(docker ps -aq --filter "label=com.docker.compose.project=$activity_name")
  activity_volumes=$(docker volume ls -q --filter "name=^${activity_name}_activity_pgdata$")
  activity_networks=$(docker network ls -q --filter "name=^${activity_name}_backend$")
  activity_images=$(docker image ls -q "$activity_name:local")
  if [[ -n "$activity_containers$activity_volumes$activity_networks$activity_images" ]]; then
    echo 'Resources for this project already exist. Nothing was started or replaced.' >&2
    exit 1
  fi
  python3 - <<'PY'
import socket
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 8480))
print('Loopback port 8480 is available.')
PY
  printf '%s\n' "$activity_release" > .bootstrap-owner
  chmod 600 .bootstrap-owner
fi

activity_compose up -d --build postgres web

activity_privileges=$(activity_compose exec -T postgres psql -U activity_admin -d activity \
  -v ON_ERROR_STOP=1 -At -c "SELECT has_table_privilege('activity_reader','work.tasks','SELECT'), has_table_privilege('activity_reader','work.tasks','INSERT'), has_table_privilege('activity_reader','work.tasks','UPDATE'), has_schema_privilege('activity_reader','work','CREATE');")
if [[ "$activity_privileges" != 't|f|f|f' ]]; then
  echo 'Unexpected reader privileges. Stopping only the new web container.' >&2
  activity_compose stop web
  exit 1
fi

python3 - <<'PY'
import json, time, urllib.request
last_error = None
for attempt in range(45):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8480/api/health', timeout=2) as response:
            health = json.load(response)
        with urllib.request.urlopen('http://127.0.0.1:8480/api/meta', timeout=2) as response:
            meta = json.load(response)
        if health.get('ok') != 1 or health.get('mode') != 'postgres' or meta.get('demo') is not False:
            raise RuntimeError('Unexpected health or demo mode')
        print('PASS: health, production mode, reader privileges.')
        print('The new database is empty until reviewed tasks are imported.')
        break
    except Exception as error:
        last_error = type(error).__name__
        time.sleep(1)
else:
    raise SystemExit('Health check failed: ' + str(last_error))
PY

activity_compose ps
printf '\nNew project is running. Existing projects and Caddy were not modified.\n'
