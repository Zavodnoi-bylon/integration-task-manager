#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
patch_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
deploy_dir=/opt/asana-telegram-alerts
[[ $EUID -eq 0 ]] || { echo 'Run this script with sudo.' >&2; exit 1; }

# Refuse to overwrite a different installed release. No secrets are read.
python3 - "$patch_dir" "$deploy_dir" <<'PY'
import hashlib,json,sys
from pathlib import Path
patch,deploy=map(Path,sys.argv[1:])
for name,digest in json.loads((patch/'original-sha256.json').read_text()).items():
    current=hashlib.sha256((deploy/'src/alerts'/name).read_bytes()).hexdigest()
    updated=hashlib.sha256((patch/name).read_bytes()).hexdigest()
    if current not in {digest,updated}:
        raise SystemExit('Installed '+name+' differs from inspected release. Stopped; no changes applied.')
print('Installed source fingerprints verified.')
PY
cd "$deploy_dir"
docker compose config --quiet
service_image="$(docker compose config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["services"]["asana-telegram-alerts"]["image"])')"
old_image="$(docker inspect --format '{{.Image}}' asana-telegram-alerts)"
backup_dir="$(mktemp -d /opt/asana-alerts-before-review-bdm-XXXXXXXX)"
cp -a src/alerts/service.py src/alerts/clients.py "$backup_dir/"
printf '%s\n' "$old_image" > "$backup_dir/image-id"
image_tag="local/asana-telegram-alerts:review-bdm-previous"
docker image tag "$old_image" "$image_tag"

rollback() {
    trap - ERR
    echo "Update failed; restoring prior code and image. Backup: $backup_dir" >&2
    cp -a "$backup_dir/service.py" "$backup_dir/clients.py" src/alerts/
    docker image tag "$old_image" "$service_image"
    docker compose up -d --no-build --no-deps asana-telegram-alerts
}
trap rollback ERR
install -o root -g root -m 0644 "$patch_dir/service.py" src/alerts/service.py
install -o root -g root -m 0644 "$patch_dir/clients.py" src/alerts/clients.py
docker compose build asana-telegram-alerts
# Test the actual built image without network, tokens, sessions or live data.
docker run --rm --network none --entrypoint python \
    -v "$patch_dir:/review-tests:ro" \
    "$service_image" \
    -m unittest discover -s /review-tests -p test_review_bdm.py -v
docker compose up -d --no-build --no-deps asana-telegram-alerts
healthy=0
for _ in $(seq 1 30); do
    health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}starting{{end}}' asana-telegram-alerts)"
    if [[ $health == healthy ]]; then healthy=1; break; fi
    if [[ $health == unhealthy ]]; then break; fi
    sleep 2
done
[[ $healthy == 1 ]] || { echo 'Health check failed.' >&2; false; }
trap - ERR
echo "REVIEW_BDM_UPDATE_READY; container healthy. Backup: $backup_dir"
echo 'First scan records section baseline without sending old Review BDM alerts.'
