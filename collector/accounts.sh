#!/bin/sh
# Run from teamactivity's interactive SSH terminal. Does not collect or schedule.
set -eu
umask 077
if [ "$(id -u)" = 0 ]; then
    echo 'Войдите в SSH как teamactivity. Не запускайте авторизацию Telegram от root.'
    exit 1
fi
BASE="${ACTIVITY_COLLECTOR_HOME:-$HOME/team-activity-ingest}"
export ACTIVITY_COLLECTOR_HOME="$BASE"
cd "$BASE/current"
exec "$BASE/.venv/bin/python" accounts.py "$@"
