#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo 'Run with sudo'; exit 1; }
out=/home/vaultadmin/asana-review-bdm-live-20261026
install -d -o vaultadmin -g vaultadmin -m 0700 "$out" "$out/installed" "$out/running"
for name in service.py clients.py; do
    install -o vaultadmin -g vaultadmin -m 0600 "/opt/asana-telegram-alerts/src/alerts/$name" "$out/installed/$name"
    docker cp "asana-telegram-alerts:/app/src/alerts/$name" "$out/running/$name"
    chown vaultadmin:vaultadmin "$out/running/$name"
    chmod 0600 "$out/running/$name"
done
docker inspect --format '{{.Image}}' asana-telegram-alerts > "$out/image-id"
chown vaultadmin:vaultadmin "$out/image-id"
chmod 0600 "$out/image-id"
echo 'ASANA_LIVE_CODE_READY (code only; secrets and data not exported)'
