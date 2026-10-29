#!/usr/bin/env bash
set -euo pipefail
umask 077

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Run with sudo: sudo bash $0" >&2
  exit 1
fi

app_root=/home/teamactivity/integration-task-manager
release="$app_root/releases/20261029Tticketing-v7"
current="$app_root/current"
database_env=/home/teamactivity/.config/integration-task-manager/database.env
sqlite_source="$app_root/data/database.db"
python="$app_root/.venv/bin/python"
units=(integration-task-manager-kalisto.service integration-task-manager-mephisto.service)

test -x "$python"
test -s "$database_env"
test -s "$sqlite_source"
test -s "$release/main.py"
test -s "$release/scripts/migrate_sqlite_to_postgres.py"
test -s "$release/scripts/verify_postgres_migration.py"
for unit in "${units[@]}"; do
  test -s "$release/deploy/$unit"
done

old_release="$(readlink -f "$current")"
test -d "$old_release"
systemd-analyze verify \
  "$release/deploy/integration-task-manager-kalisto.service" \
  "$release/deploy/integration-task-manager-mephisto.service"

cutover_started=0
success=0
rollback() {
  exit_code=$?
  if [[ $success -eq 0 && $cutover_started -eq 1 ]]; then
    systemctl stop "${units[@]}" >/dev/null 2>&1 || true
    ln -sfn "$old_release" "$app_root/current.rollback"
    mv -Tf "$app_root/current.rollback" "$current"
    for unit in "${units[@]}"; do
      if [[ -s "$old_release/deploy/$unit" ]]; then
        install -m 0644 "$old_release/deploy/$unit" "/etc/systemd/system/$unit"
      fi
    done
    systemctl daemon-reload
    systemctl start "${units[@]}" >/dev/null 2>&1 || true
    echo "POSTGRES_CUTOVER_ROLLED_BACK" >&2
  fi
  exit "$exit_code"
}
trap rollback EXIT

systemctl stop "${units[@]}"
cutover_started=1

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_dir="$app_root/backups/$timestamp"
install -d -m 0700 -o teamactivity -g teamactivity "$backup_dir"
runuser -u teamactivity -- "$python" - "$sqlite_source" "$backup_dir/database.db" <<'PY'
import sqlite3
import sys

source = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
target = sqlite3.connect(sys.argv[2])
try:
    source.backup(target)
finally:
    target.close()
    source.close()
PY
chown teamactivity:teamactivity "$backup_dir/database.db"
chmod 0600 "$backup_dir/database.db"
sha256sum "$backup_dir/database.db" > "$backup_dir/database.db.sha256"
chown teamactivity:teamactivity "$backup_dir/database.db.sha256"

runuser -u teamactivity -- bash -c '
  set -euo pipefail
  set -a
  source "$1"
  set +a
  cd "$2"
  "$3" scripts/migrate_sqlite_to_postgres.py "$4"
  "$3" scripts/verify_postgres_migration.py "$4"
' cutover "$database_env" "$release" "$python" "$sqlite_source"

ln -sfn "$release" "$app_root/current.next"
mv -Tf "$app_root/current.next" "$current"
for unit in "${units[@]}"; do
  install -m 0644 "$release/deploy/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable "${units[@]}" >/dev/null
systemctl start "${units[@]}"

for _ in {1..10}; do
  if systemctl is-active --quiet "${units[0]}" \
     && systemctl is-active --quiet "${units[1]}"; then
    break
  fi
  sleep 1
done
systemctl is-active --quiet "${units[0]}"
systemctl is-active --quiet "${units[1]}"
sleep 2
systemctl is-active --quiet "${units[0]}"
systemctl is-active --quiet "${units[1]}"

success=1
trap - EXIT
echo "POSTGRES_CUTOVER_READY backup=$backup_dir/database.db release=$release"
