#!/usr/bin/env sh
set -eu

API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-8000}"
WEB_HOST="${WEB_HOST:-0.0.0.0}"
WEB_PORT="${WEB_PORT:-3000}"
DS160_PROCESS="${DS160_PROCESS:-combined}"

cd /app
mkdir -p /data

start_api() {
  exec uvicorn app.main:app --host "$API_HOST" --port "$API_PORT"
}

start_web() {
  cd /app/web
  export HOSTNAME="$WEB_HOST"
  export PORT="$WEB_PORT"
  exec node server.js
}

start_worker() {
  exec python -m app.cli.main run-parse-worker
}

case "$DS160_PROCESS" in
  api)
    start_api
    ;;
  web)
    start_web
    ;;
  worker)
    start_worker
    ;;
  combined)
    ;;
  *)
    echo "Unsupported DS160_PROCESS: $DS160_PROCESS" >&2
    exit 64
    ;;
esac

uvicorn app.main:app --host "$API_HOST" --port "$API_PORT" &
API_PID="$!"
WEB_PID=""

cleanup() {
  if [ -n "$API_PID" ]; then
    kill "$API_PID" 2>/dev/null || true
  fi
  if [ -n "$WEB_PID" ]; then
    kill "$WEB_PID" 2>/dev/null || true
  fi
}
trap cleanup INT TERM

cd /app/web
export HOSTNAME="$WEB_HOST"
export PORT="$WEB_PORT"
node server.js &
WEB_PID="$!"

while :; do
  if ! kill -0 "$API_PID" 2>/dev/null; then
    set +e
    wait "$API_PID"
    EXIT_CODE="$?"
    set -e
    cleanup
    exit "$EXIT_CODE"
  fi

  if ! kill -0 "$WEB_PID" 2>/dev/null; then
    set +e
    wait "$WEB_PID"
    EXIT_CODE="$?"
    set -e
    cleanup
    exit "$EXIT_CODE"
  fi

  sleep 1
done
