#!/bin/sh
set -eu
umask 077
BASE="$HOME/team-activity-ingest"
export ACTIVITY_HOME="$BASE"
mkdir -p "$BASE/logs"
# Prevent overlap including log rotation and backups.
exec 9>"$BASE/scheduler.lock"
flock -n 9 || exit 0
LOG="$BASE/logs/ingest.log"
if [ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 1048576 ]; then
    mv -f "$LOG" "$LOG.1"
fi
cd "$BASE/current"
exec >>"$LOG" 2>&1
date -u '+Run %Y-%m-%dT%H:%M:%SZ'
"$BASE/.venv/bin/python" ingest.py run
# SQLite online backup; keep the last 14 successful daily snapshots.
DAY=$(date -u +%Y%m%d)
if [ ! -f "$BASE/backups/daily-$DAY.sqlite3" ]; then
    "$BASE/.venv/bin/python" backup_daily.py
fi
