#!/usr/bin/env bash
set -euo pipefail

# Lightweight frontend-only deploy for small UI/copy changes.
# Builds the Next.js standalone artifact locally, uploads it to the server,
# copies it into the existing ds160-web container, and restarts only ds160-web.
# This intentionally avoids `docker compose up --build` on the production host.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_DIR="$ROOT_DIR/web"
SHA="${NEXT_PUBLIC_GIT_SHA:-$(git -C "$ROOT_DIR" rev-parse --short HEAD)}"
BUILD_TIME="${NEXT_PUBLIC_BUILD_TIME:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"
APP_VERSION="${NEXT_PUBLIC_APP_VERSION:-0.1.2}"
DEPLOY_HOST="${DS160_DEPLOY_HOST:-root@conectv6.302dog.icu}"
SSH_KEY="${DS160_DEPLOY_KEY:-$HOME/.ssh/ds160_deploy_ed25519}"
REMOTE_ROOT="${DS160_REMOTE_ROOT:-/opt/ds160-agent2}"
WEB_CONTAINER="${DS160_WEB_CONTAINER:-ds160-web}"
ARTIFACT="/tmp/ds160-web-${SHA}.tgz"
REMOTE_ARTIFACT="$REMOTE_ROOT/.hotfix-web-${SHA}.tgz"
REMOTE_EXTRACT_DIR="$REMOTE_ROOT/.hotfix-web-${SHA}"

if [[ ! -f "$SSH_KEY" ]]; then
  echo "Missing SSH key: $SSH_KEY" >&2
  exit 1
fi

cd "$WEB_DIR"
echo "[web-hotfix] building frontend artifact sha=$SHA time=$BUILD_TIME"
NEXT_PUBLIC_APP_VERSION="$APP_VERSION" \
NEXT_PUBLIC_API_BASE_URL="${NEXT_PUBLIC_API_BASE_URL:-/api}" \
NEXT_PUBLIC_MOCK="${NEXT_PUBLIC_MOCK:-false}" \
NEXT_PUBLIC_GIT_SHA="$SHA" \
NEXT_PUBLIC_BUILD_TIME="$BUILD_TIME" \
pnpm build

echo "[web-hotfix] packaging $ARTIFACT"
tar -C "$WEB_DIR" -czf "$ARTIFACT" .next/standalone .next/static public

echo "[web-hotfix] uploading to $DEPLOY_HOST:$REMOTE_ARTIFACT"
scp -i "$SSH_KEY" -o BatchMode=yes -o ConnectTimeout=20 "$ARTIFACT" "$DEPLOY_HOST:$REMOTE_ARTIFACT"

echo "[web-hotfix] applying artifact to $WEB_CONTAINER without rebuilding Docker image"
ssh -i "$SSH_KEY" -o BatchMode=yes -o ConnectTimeout=20 "$DEPLOY_HOST" \
  "set -euo pipefail
   cd '$REMOTE_ROOT'
   rm -rf '$REMOTE_EXTRACT_DIR'
   mkdir -p '$REMOTE_EXTRACT_DIR'
   tar -xzf '$REMOTE_ARTIFACT' -C '$REMOTE_EXTRACT_DIR'
   docker stop '$WEB_CONTAINER' >/dev/null
   docker cp '$REMOTE_EXTRACT_DIR/.next/standalone/.' '$WEB_CONTAINER:/app/web/'
   docker cp '$REMOTE_EXTRACT_DIR/.next/static/.' '$WEB_CONTAINER:/app/web/.next/static/'
   docker cp '$REMOTE_EXTRACT_DIR/public/.' '$WEB_CONTAINER:/app/web/public/'
   docker start '$WEB_CONTAINER' >/dev/null
   for i in 1 2 3 4 5 6 7 8; do
     status=\$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' '$WEB_CONTAINER')
     echo \"[web-hotfix] web_health=\$status\"
     [ \"\$status\" = healthy ] && exit 0
     sleep 3
   done
   docker logs --tail=80 '$WEB_CONTAINER' >&2
   exit 1"

echo "[web-hotfix] done: frontend artifact $SHA applied to $WEB_CONTAINER"
