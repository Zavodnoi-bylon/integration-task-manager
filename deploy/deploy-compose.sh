#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Run as root: sudo bash deploy/deploy-compose.sh" >&2
  exit 1
fi

source_dir=${SOURCE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
config_dir=${CONFIG_DIR:-/home/teamactivity/.config/integration-task-manager}
database_network=${DATABASE_NETWORK:-team-activity-trial_backend}
project_name=${COMPOSE_PROJECT_NAME:-integration-task-manager}
image_tag=${IMAGE_TAG:-$(date -u +%Y%m%dT%H%M%SZ)}
test_image="integration-task-manager:test-${image_tag}"

export CONFIG_DIR="$config_dir"
export DATABASE_NETWORK="$database_network"
export COMPOSE_PROJECT_NAME="$project_name"
export IMAGE_TAG="$image_tag"

compose=(docker compose --project-name "$project_name" --file "$source_dir/compose.yaml")
legacy_units=(
  integration-task-manager-kalisto.service
  integration-task-manager-mephisto.service
)
legacy_was_active=()
cutover_started=0
success=0

cleanup_test_image() {
  docker image rm --force "$test_image" >/dev/null 2>&1 || true
}

rollback() {
  local exit_code=$?
  cleanup_test_image
  if [[ $cutover_started -eq 1 && $success -eq 0 ]]; then
    echo "Container cutover failed; restoring legacy systemd services." >&2
    "${compose[@]}" down --remove-orphans >/dev/null 2>&1 || true
    for index in "${!legacy_units[@]}"; do
      if [[ ${legacy_was_active[$index]:-0} -eq 1 ]]; then
        systemctl enable --now "${legacy_units[$index]}" >/dev/null 2>&1 || true
      fi
    done
  fi
  exit "$exit_code"
}
trap rollback ERR INT TERM
trap cleanup_test_image EXIT

for file in database.env kalisto.env mephisto.env; do
  path="$config_dir/$file"
  if [[ ! -s "$path" ]]; then
    echo "Missing configuration: $path" >&2
    exit 1
  fi
  if grep -Eq 'replace_with_|change_me' "$path"; then
    echo "Configuration contains a placeholder: $path" >&2
    exit 1
  fi
done

grep -Eq '^BOT_KEY=kalisto\r?$' "$config_dir/kalisto.env" || {
  echo "kalisto.env must contain BOT_KEY=kalisto" >&2
  exit 1
}
grep -Eq '^BOT_KEY=mephisto\r?$' "$config_dir/mephisto.env" || {
  echo "mephisto.env must contain BOT_KEY=mephisto" >&2
  exit 1
}

docker network inspect "$database_network" >/dev/null
"${compose[@]}" config --quiet

echo "Building isolated test image..."
docker build --target test --tag "$test_image" "$source_dir"
docker run --rm --network none "$test_image"

echo "Building production image..."
"${compose[@]}" build

echo "Checking PostgreSQL schema through the private Docker network..."
"${compose[@]}" run --rm --no-deps --entrypoint python kalisto -c \
  "from app import storage; storage.create_tables(); print('DATABASE_READY')"

for unit in "${legacy_units[@]}"; do
  if systemctl is-active --quiet "$unit"; then
    legacy_was_active+=(1)
  else
    legacy_was_active+=(0)
  fi
done

cutover_started=1
systemctl stop "${legacy_units[@]}"
"${compose[@]}" up --detach --remove-orphans

stable_checks=0
for _ in $(seq 1 15); do
  running=0
  clean=1
  for service in kalisto mephisto; do
    container_id=$("${compose[@]}" ps --quiet "$service")
    if [[ -n "$container_id" ]] && \
       [[ $(docker inspect --format '{{.State.Running}}' "$container_id") == true ]]; then
      running=$((running + 1))
      if [[ $(docker inspect --format '{{.RestartCount}}' "$container_id") -ne 0 ]]; then
        clean=0
      fi
    fi
  done
  if [[ $running -eq 2 && $clean -eq 1 ]]; then
    stable_checks=$((stable_checks + 1))
    if [[ $stable_checks -ge 5 ]]; then
      break
    fi
  else
    stable_checks=0
  fi
  sleep 2
done

if [[ $stable_checks -lt 5 ]]; then
  "${compose[@]}" ps
  "${compose[@]}" logs --no-color --tail 80
  false
fi

for service in kalisto mephisto; do
  if ! "${compose[@]}" logs --no-color "$service" 2>&1 | grep -q 'Start polling'; then
    echo "Polling did not start for $service" >&2
    "${compose[@]}" logs --no-color --tail 80 "$service" >&2
    false
  fi
done

systemctl disable "${legacy_units[@]}" >/dev/null
success=1
trap - ERR INT TERM
cleanup_test_image
trap - EXIT

"${compose[@]}" ps
echo "TASK_MANAGER_COMPOSE_READY image_tag=$image_tag legacy_units=disabled"
