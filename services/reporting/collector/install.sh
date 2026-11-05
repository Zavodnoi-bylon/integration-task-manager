#!/bin/sh
# Run as vaultadmin with an extracted, code-only release directory as argument.
set -eu
umask 077
RELEASE=$(realpath "$1")
BASE="$HOME/team-activity-ingest"
case "$RELEASE" in "$BASE"/releases/*) ;; *) echo 'Invalid release path'; exit 1;; esac
mkdir -p "$BASE/data/inbox" "$BASE/data/normalized" "$BASE/data/session" "$BASE/backups" "$BASE/logs"
chmod 700 "$BASE" "$BASE/data" "$BASE/data/inbox" "$BASE/data/normalized" "$BASE/data/session" "$BASE/backups" "$BASE/logs"
# Existing crontab is preserved verbatim, except for this project's own marked block.
STAMP=$(date -u +%Y%m%dT%H%M%S)
crontab -l >"$BASE/backups/crontab-$STAMP.txt" 2>/dev/null || true
if [ ! -x "$BASE/.venv/bin/python" ]; then
    python3 -m venv --without-pip "$BASE/.venv"
fi
if ! "$BASE/.venv/bin/python" -c 'import telethon; assert telethon.__version__ == "1.45.0"' 2>/dev/null; then
    python3 -c 'import urllib.request,sys; urllib.request.urlretrieve("https://bootstrap.pypa.io/pip/pip.pyz", sys.argv[1])' "$BASE/pip.pyz"
    "$BASE/.venv/bin/python" "$BASE/pip.pyz" install --disable-pip-version-check -r "$RELEASE/requirements.txt"
fi
"$BASE/.venv/bin/python" -m unittest discover -s "$RELEASE" -v
chmod 700 "$RELEASE/run.sh" "$RELEASE/setup-telegram.sh"
if [ -L "$BASE/current" ]; then
    readlink "$BASE/current" >"$BASE/backups/previous-release-$STAMP.txt"
fi
ln -s "$RELEASE" "$BASE/current.next"
mv -Tf "$BASE/current.next" "$BASE/current"
python3 - "$BASE" "$STAMP" <<'PY'
import pathlib, sys
base = pathlib.Path(sys.argv[1])
lines = (base / 'backups' / ('crontab-' + sys.argv[2] + '.txt')).read_text().splitlines()
out, skip = [], False
for line in lines:
    if line == '# BEGIN team-activity-ingest':
        skip = True
    elif line == '# END team-activity-ingest':
        skip = False
    elif not skip:
        out.append(line)
out += ['# BEGIN team-activity-ingest', '*/15 * * * * /home/vaultadmin/team-activity-ingest/current/run.sh', '# END team-activity-ingest']
(base / 'cron.new').write_text('\n'.join(out) + '\n')
PY
crontab "$BASE/cron.new"
"$BASE/current/run.sh"
echo 'Installed; scheduled every 15 minutes. Telegram remains disabled until setup.'
