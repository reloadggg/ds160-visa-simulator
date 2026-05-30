#!/usr/bin/env bash
set -Eeuo pipefail

HOST_HEADER="${HOST_HEADER:-ds160.efastt.store}"
BACKUP_ROOT="${BACKUP_ROOT:-.deploy-backups}"

run() {
  printf '+'
  printf ' %q' "$@"
  printf '\n'
  "$@"
}

backup_path() {
  date -u +"%Y%m%dT%H%M%SZ"
}

main() {
  local backup_dir
  backup_dir="$BACKUP_ROOT/$(backup_path)-combined-recovery"
  run mkdir -p "$backup_dir"
  run chmod 700 "$backup_dir"

  run docker ps --format "{{.Names}} {{.Status}}" | tee "$backup_dir/docker-before.txt"
  run docker compose ps | tee "$backup_dir/compose-before.txt"

  docker compose stop ds160-worker ds160-api ds160-web postgres >/dev/null 2>&1 || true
  run docker start ds160-agent2

  if ! docker ps --format "{{.Names}}" | grep -qx "ds160-nginx"; then
    cat >&2 <<EOF
ds160-nginx is not running. This recovery script did not start it because the
checked-out nginx config targets split services. Restore/reload the known-good
combined nginx config before starting nginx.
EOF
  fi

  run docker ps --format "{{.Names}} {{.Status}}" | tee "$backup_dir/docker-after.txt"
  run docker compose ps | tee "$backup_dir/compose-after.txt"
  run curl -k -fsS "https://127.0.0.1:18000/healthz" -H "Host: $HOST_HEADER"
  run curl --noproxy "*" -fsS "https://$HOST_HEADER/healthz"

  echo "Combined recovery checks completed. Evidence directory: $backup_dir"
}

main "$@"
