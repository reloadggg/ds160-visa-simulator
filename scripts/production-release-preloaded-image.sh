#!/usr/bin/env bash
set -euo pipefail

RELEASE_IMAGE="${RELEASE_IMAGE:-}"
APP_IMAGE_TAG="${APP_IMAGE_TAG:-ds160-agent2:latest}"
APP_GIT_SHA="${APP_GIT_SHA:-$(git rev-parse --short HEAD)}"
APP_BUILD_TIME="${APP_BUILD_TIME:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"
ENV_FILE="${ENV_FILE:-.env}"
SERVICES=(postgres ds160-api ds160-web ds160-worker nginx)

usage() {
  cat <<'USAGE'
Usage:
  RELEASE_IMAGE=ds160-agent2:<sha> scripts/production-release-preloaded-image.sh

Run this on the production host after a prebuilt image has already been loaded
with `docker load`. The script never builds on the production host; it tags the
preloaded image as ds160-agent2:latest, records release metadata in .env, and
recreates the Compose services with --no-build.

Environment:
  RELEASE_IMAGE   Loaded image tag to promote. Required unless it already is ds160-agent2:latest.
  APP_IMAGE_TAG   Compose image tag to update. Default: ds160-agent2:latest.
  APP_GIT_SHA     Release sha for runtime /api/version. Default: git rev-parse --short HEAD.
  APP_BUILD_TIME  Release build time. Default: current UTC time.
  ENV_FILE        Env file to update. Default: .env.
USAGE
}

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
  usage
  exit 0
fi

if [ -n "$RELEASE_IMAGE" ]; then
  if ! docker image inspect "$RELEASE_IMAGE" >/dev/null 2>&1; then
    echo "Release image not found locally: $RELEASE_IMAGE" >&2
    echo "Load it first, e.g. gunzip -c ds160-agent2-${APP_GIT_SHA}.tar.gz | docker load" >&2
    exit 66
  fi
  if [ "$RELEASE_IMAGE" != "$APP_IMAGE_TAG" ]; then
    docker tag "$RELEASE_IMAGE" "$APP_IMAGE_TAG"
  fi
elif ! docker image inspect "$APP_IMAGE_TAG" >/dev/null 2>&1; then
  echo "No RELEASE_IMAGE set and $APP_IMAGE_TAG does not exist locally." >&2
  usage >&2
  exit 64
fi

if [ ! -f "$ENV_FILE" ]; then
  touch "$ENV_FILE"
  chmod 600 "$ENV_FILE"
fi

python3 - "$ENV_FILE" "$APP_GIT_SHA" "$APP_BUILD_TIME" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path

path = Path(sys.argv[1])
sha = sys.argv[2]
build_time = sys.argv[3]
updates = {
    "APP_GIT_SHA": sha,
    "APP_BUILD_TIME": build_time,
    "NEXT_PUBLIC_GIT_SHA": sha,
    "NEXT_PUBLIC_BUILD_TIME": build_time,
}
lines = path.read_text(encoding="utf-8").splitlines()
seen: set[str] = set()
out: list[str] = []
for line in lines:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in line:
        out.append(line)
        continue
    key = line.split("=", 1)[0].strip()
    if key in updates:
        out.append(f"{key}={updates[key]}")
        seen.add(key)
    else:
        out.append(line)
for key, value in updates.items():
    if key not in seen:
        out.append(f"{key}={value}")
path.write_text("\n".join(out) + "\n", encoding="utf-8")
PY

printf 'release_sha=%s\nrelease_build_time=%s\n' "$APP_GIT_SHA" "$APP_BUILD_TIME"
docker compose up -d --no-build "${SERVICES[@]}"
docker compose ps
