#!/usr/bin/env bash
set -euo pipefail

source_dir=${SOURCE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
runtime_env=${RUNTIME_ENV_FILE:-/home/teamactivity/integration-task-manager/runtime.env}

if [[ ! -r "$runtime_env" ]]; then
  echo "Missing runtime state: $runtime_env" >&2
  exit 1
fi

set -a
# Contains only paths, network/project names and the immutable image tag.
source "$runtime_env"
set +a

exec docker compose \
  --project-name "${COMPOSE_PROJECT_NAME:-integration-task-manager}" \
  --file "$source_dir/compose.yaml" \
  "$@"
