#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo 'Run this installer through sudo.' >&2
  exit 77
fi

readonly project_root=/home/teamactivity/team-activity-explorer
readonly config_dir=/etc/team-activity-backup
readonly state_dir=/var/lib/team-activity-backup
readonly source_key=/home/teamactivity/.ssh/team_activity_backup_ed25519
readonly identity_file="$config_dir/id_ed25519"
readonly password_file="$config_dir/restic-password"
readonly known_hosts_file="$config_dir/known_hosts"
readonly env_file="$config_dir/restic.env"
readonly target_host_key='167.99.36.155 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIITPnkb2/ZNJJWy3w2DfVDaZXGKl1fxvNs/zo5SXPfVv'

for source_script in offsite-backup.sh verify-offsite-backup.sh; do
  test -r "$project_root/scripts/$source_script" || {
    echo "Missing $project_root/scripts/$source_script" >&2
    exit 66
  }
done

test -s "$source_key" || {
  echo "Missing backup SSH key: $source_key" >&2
  exit 66
}

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends restic postgresql-client

install -d -o root -g root -m 0700 "$config_dir" "$state_dir" "$state_dir/staging" "$state_dir/cache"
install -o root -g root -m 0600 "$source_key" "$identity_file"
install -o root -g root -m 0750 "$project_root/scripts/offsite-backup.sh" /usr/local/sbin/team-activity-offsite-backup
install -o root -g root -m 0750 "$project_root/scripts/verify-offsite-backup.sh" /usr/local/sbin/team-activity-offsite-verify

if [[ ! -s "$password_file" ]]; then
  openssl rand -base64 48 >"$password_file"
  chmod 0600 "$password_file"
  echo 'A new restic repository password was generated.'
  echo 'After setup, store it in the corporate password manager; never send it in chat.'
fi

printf '%s\n' "$target_host_key" >"$known_hosts_file"
chmod 0600 "$known_hosts_file"

cat >"$env_file" <<EOF
RESTIC_REPOSITORY='sftp:ta-backup@167.99.36.155:/restic'
RESTIC_PASSWORD_FILE='$password_file'
RESTIC_CACHE_DIR='$state_dir/cache'
RESTIC_SFTP_COMMAND='ssh -i $identity_file -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$known_hosts_file'
EOF
chmod 0600 "$env_file"

install -o root -g root -m 0644 "$project_root/deploy/systemd/team-activity-offsite-backup.service" /etc/systemd/system/team-activity-offsite-backup.service
install -o root -g root -m 0644 "$project_root/deploy/systemd/team-activity-offsite-backup.timer" /etc/systemd/system/team-activity-offsite-backup.timer
install -o root -g root -m 0644 "$project_root/deploy/systemd/team-activity-offsite-verify.service" /etc/systemd/system/team-activity-offsite-verify.service
install -o root -g root -m 0644 "$project_root/deploy/systemd/team-activity-offsite-verify.timer" /etc/systemd/system/team-activity-offsite-verify.timer

systemctl daemon-reload

set -a
# shellcheck disable=SC1090
source "$env_file"
set +a

if ! restic -o "sftp.command=$RESTIC_SFTP_COMMAND" snapshots >/dev/null 2>&1; then
  restic -o "sftp.command=$RESTIC_SFTP_COMMAND" init
fi

systemctl enable --now team-activity-offsite-backup.timer
systemctl enable --now team-activity-offsite-verify.timer
systemctl start team-activity-offsite-backup.service

echo 'TEAM_ACTIVITY_OFFSITE_BACKUP_READY'
echo "Recovery password file: $password_file"
echo 'Copy that password to the corporate password manager from the server terminal.'
