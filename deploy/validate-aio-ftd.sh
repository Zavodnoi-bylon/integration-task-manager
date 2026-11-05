#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Run as root: sudo bash deploy/validate-aio-ftd.sh" >&2
  exit 1
fi

source_dir=${SOURCE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
image=${AIO_FTD_TEST_IMAGE:-integration-task-manager:aio-ftd-mvp-test}

docker build --target test --tag "$image" "$source_dir"
docker run --rm --network none "$image"

echo "AIO_FTD_TESTS_OK"
