#!/usr/bin/env bash
# Ordinary SSH user, no Docker socket/sudo. Explicitly targets the trial DB.
set -Eeuo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
set -a
source .env.client
set +a
export ACTIVITY_WRITER_DB_HOST=127.0.0.1
export ACTIVITY_WRITER_DB_PORT=55432
export ACTIVITY_WRITER_DB_NAME=activity_trial
export ACTIVITY_WRITER_DB_USER=activity_writer
export ACTIVITY_WRITER_DB_PASSWORD="$ACTIVITY_WRITER_PASSWORD"
unset ACTIVITY_ADMIN_PASSWORD ACTIVITY_READER_PASSWORD ACTIVITY_WRITER_PASSWORD
unset ACTIVITY_WRITER_DATABASE_URL
exec .venv/bin/python sync.py "$@"
