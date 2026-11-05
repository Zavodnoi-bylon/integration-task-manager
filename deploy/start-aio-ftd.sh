#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Run as root: sudo bash deploy/start-aio-ftd.sh" >&2
  exit 1
fi

source_dir=${SOURCE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
compose=(bash "$source_dir/deploy/compose.sh" --profile aio-ftd)

bash "$source_dir/deploy/apply-aio-ftd-migration.sh"
"${compose[@]}" build aio-ftd

# The first cycle creates a checkpoint at the current moment and deliberately
# does not enqueue historical zero-revenue conversions.
"${compose[@]}" run --rm aio-ftd python aio_ftd_main.py --once
"${compose[@]}" up --detach aio-ftd
"${compose[@]}" ps aio-ftd
"${compose[@]}" logs --tail 20 aio-ftd

echo "AIO_FTD_MONITOR_STARTED"
