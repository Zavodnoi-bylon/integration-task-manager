#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Run as root: sudo bash deploy/reschedule-aio-ftd.sh" >&2
  exit 1
fi

source_dir=${SOURCE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
config_dir=${CONFIG_DIR:-/home/teamactivity/.config/integration-task-manager}
env_file="$config_dir/aio-ftd.env"
compose=(bash "$source_dir/deploy/compose.sh" --profile aio-ftd)

if [[ ! -f "$env_file" ]]; then
  echo "Missing AIO FTD configuration: $env_file" >&2
  exit 1
fi

sed -i \
  -e 's/^AIO_POLL_SECONDS=.*/AIO_POLL_SECONDS=600/' \
  -e 's/^AIO_POLL_OFFSET_SECONDS=.*/AIO_POLL_OFFSET_SECONDS=120/' \
  "$env_file"
if ! grep -q '^AIO_POLL_OFFSET_SECONDS=' "$env_file"; then
  printf '\nAIO_POLL_OFFSET_SECONDS=120\n' >>"$env_file"
fi
chmod 600 "$env_file"

"${compose[@]}" build aio-ftd
"${compose[@]}" up --detach --no-deps aio-ftd
"${compose[@]}" ps aio-ftd
"${compose[@]}" logs --tail 30 aio-ftd

echo "AIO_FTD_RESCHEDULED interval=600 offset=120"
