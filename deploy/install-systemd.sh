#!/usr/bin/env bash
set -euo pipefail

app_root=/home/teamactivity/integration-task-manager
profile=${1:-kalisto}

case "$profile" in
  kalisto|mephisto) ;;
  *)
    echo "Unknown profile: $profile (expected kalisto or mephisto)" >&2
    exit 1
    ;;
esac

env_file="/home/teamactivity/.config/integration-task-manager/$profile.env"
unit_name="integration-task-manager-$profile.service"
unit_source="$app_root/current/deploy/$unit_name"
unit_target="/etc/systemd/system/$unit_name"

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Run with sudo: sudo bash $app_root/current/deploy/install-systemd.sh $profile" >&2
  exit 1
fi

if [[ ! -s "$env_file" ]]; then
  echo "Missing configuration: $env_file" >&2
  exit 1
fi

if grep -q 'replace_with_' "$env_file"; then
  echo "Configuration still contains placeholders: $env_file" >&2
  exit 1
fi

install -m 0644 "$unit_source" "$unit_target"
systemctl daemon-reload
systemctl enable "$unit_name"
systemctl restart "$unit_name"
systemctl status "$unit_name" --no-pager
