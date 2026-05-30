#!/usr/bin/env bash
set -Eeuo pipefail

CONFIRM_VALUE="I_UNDERSTAND_PRODUCTION_CUTOVER"
CONFIRM_PRODUCTION_CUTOVER="${CONFIRM_PRODUCTION_CUTOVER:-}"
DEPLOY_BRANCH="${DEPLOY_BRANCH:-refactor/agent-runtime-graph}"
HOST_HEADER="${HOST_HEADER:-ds160.efastt.store}"
BACKUP_ROOT="${BACKUP_ROOT:-.deploy-backups}"
RUN_WRITE_MIGRATION="${RUN_WRITE_MIGRATION:-0}"
TRUNCATE_TARGET="${TRUNCATE_TARGET:-0}"
SKIP_GIT_PULL="${SKIP_GIT_PULL:-0}"
SKIP_DOCKER_BUILD="${SKIP_DOCKER_BUILD:-0}"
ALLOW_DIRTY_WORKTREE="${ALLOW_DIRTY_WORKTREE:-0}"
TARGET_DATABASE_URL="${COMPOSE_DATABASE_URL:-postgresql+psycopg://ds160:ds160@postgres:5432/ds160}"

require_confirmed_cutover() {
  if [ "$CONFIRM_PRODUCTION_CUTOVER" != "$CONFIRM_VALUE" ]; then
    cat >&2 <<EOF
Refusing to run production cutover.

Set:
  CONFIRM_PRODUCTION_CUTOVER=$CONFIRM_VALUE

Optional:
  RUN_WRITE_MIGRATION=1      required; run the real SQLite -> Postgres copy after dry-run
  TRUNCATE_TARGET=1          pass --truncate-target to the write migration
  SKIP_GIT_PULL=1            skip git fetch/pull when code is already in place
  SKIP_DOCKER_BUILD=1        start services from a preloaded image instead of building on host
  ALLOW_DIRTY_WORKTREE=1     allow local server changes during cutover
EOF
    exit 64
  fi
  if [ "$RUN_WRITE_MIGRATION" != "1" ]; then
    cat >&2 <<EOF
Refusing to run a partial production cutover.

This script stops the old combined container and starts split services, so it is
only safe as a full cutover. Set RUN_WRITE_MIGRATION=1 to run dry-run first and
then the real migration in the same maintenance window.
EOF
    exit 64
  fi
}

run() {
  printf '+'
  printf ' %q' "$@"
  printf '\n'
  "$@"
}

backup_path() {
  date -u +"%Y%m%dT%H%M%SZ"
}

write_env_presence_report() {
  local output_file="$1"
  : > "$output_file"
  for key in \
    OPENAI_BASE_URL \
    OPENAI_API_KEY \
    APP_AUTH_PASSWORD \
    COMPOSE_DATABASE_URL \
    APP_GIT_SHA \
    APP_BUILD_TIME \
    NEXT_PUBLIC_GIT_SHA \
    NEXT_PUBLIC_BUILD_TIME
  do
    if grep -q "^${key}=" .env 2>/dev/null; then
      printf '%s=present\n' "$key" >> "$output_file"
    else
      printf '%s=missing\n' "$key" >> "$output_file"
    fi
  done
}

copy_sqlite_backup_from_combined_volume() {
  local backup_dir="$1"
  if docker compose ps --services --all | grep -qx "ds160-agent2"; then
    run docker compose --profile combined stop ds160-agent2
    run docker compose --profile combined run \
      --rm \
      --no-deps \
      -v "$PWD/$backup_dir:/backup" \
      ds160-agent2 \
      sh -lc 'test -s /data/app.sqlite3 && cp /data/app.sqlite3 /backup/app.sqlite3.backup'
  else
    echo "No ds160-agent2 service in current compose file; expecting existing backup at $backup_dir/app.sqlite3.backup"
  fi
}

run_migration() {
  local backup_dir="$1"
  local mode="$2"
  local args=(
    /app/.venv/bin/python
    -m app.cli.main
    migrate-sqlite-to-postgres
    --source-url sqlite:////tmp/app.sqlite3
    --target-url "$TARGET_DATABASE_URL"
  )
  if [ "$mode" = "dry-run" ]; then
    args+=(--dry-run)
  elif [ "$TRUNCATE_TARGET" = "1" ]; then
    args+=(--truncate-target)
  fi

  run docker compose exec -T ds160-api "${args[@]}" | tee "$backup_dir/migration-${mode}.json"
}

start_split_services() {
  local backup_dir="$1"
  if [ "$SKIP_DOCKER_BUILD" = "1" ]; then
    echo "skip_docker_build=1" | tee "$backup_dir/build-mode.txt"
    run docker compose up -d postgres ds160-api ds160-web ds160-worker
    return
  fi

  echo "skip_docker_build=0" | tee "$backup_dir/build-mode.txt"
  run docker compose up -d --build postgres ds160-api ds160-web ds160-worker
}

main() {
  require_confirmed_cutover

  local backup_dir
  backup_dir="$BACKUP_ROOT/$(backup_path)-split-postgres-cutover"
  run mkdir -p "$backup_dir"
  run chmod 700 "$backup_dir"

  if [ "$ALLOW_DIRTY_WORKTREE" != "1" ] && [ -n "$(git status --short)" ]; then
    git status --short >&2
    echo "Refusing cutover with a dirty server worktree." >&2
    exit 65
  fi
  run git status --short
  run git rev-parse HEAD | tee "$backup_dir/git-head.txt"
  run docker compose ps | tee "$backup_dir/compose-before.txt"
  write_env_presence_report "$backup_dir/env-presence.txt"
  if [ -f .env ]; then
    run cp .env "$backup_dir/env.backup"
    run chmod 600 "$backup_dir/env.backup"
  fi

  copy_sqlite_backup_from_combined_volume "$backup_dir"

  if [ "$SKIP_GIT_PULL" != "1" ]; then
    run git fetch origin "$DEPLOY_BRANCH"
    run git pull --ff-only origin "$DEPLOY_BRANCH"
  fi

  export APP_GIT_SHA="${APP_GIT_SHA:-$(git rev-parse --short HEAD)}"
  export APP_BUILD_TIME="${APP_BUILD_TIME:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"
  export NEXT_PUBLIC_GIT_SHA="${NEXT_PUBLIC_GIT_SHA:-$APP_GIT_SHA}"
  export NEXT_PUBLIC_BUILD_TIME="${NEXT_PUBLIC_BUILD_TIME:-$APP_BUILD_TIME}"

  run docker compose config --quiet
  start_split_services "$backup_dir"
  run docker cp "$backup_dir/app.sqlite3.backup" ds160-api:/tmp/app.sqlite3
  run_migration "$backup_dir" "dry-run"

  run_migration "$backup_dir" "write"

  run docker compose exec -T ds160-api rm -f /tmp/app.sqlite3
  run docker compose up -d nginx
  run docker compose ps
  run curl -k -fsS "https://127.0.0.1:18000/healthz" -H "Host: $HOST_HEADER"
  run curl -k -fsS "https://127.0.0.1:18000/api/version" -H "Host: $HOST_HEADER"
  run curl --noproxy '*' -fsS "https://$HOST_HEADER/healthz"

  echo "Cutover checks completed. Evidence directory: $backup_dir"
}

main "$@"
