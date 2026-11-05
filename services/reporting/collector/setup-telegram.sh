#!/bin/sh
set -eu
umask 077
BASE="$HOME/team-activity-ingest"
export ACTIVITY_HOME="$BASE"
cd "$BASE/current"
"$BASE/.venv/bin/python" ingest.py setup
"$BASE/current/run.sh"
"$BASE/.venv/bin/python" ingest.py status
