#!/usr/bin/env sh
set -eu

API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-8000}"
WEB_HOST="${WEB_HOST:-0.0.0.0}"
WEB_PORT="${WEB_PORT:-3000}"

cd /app
mkdir -p /data

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
node server.js --hostname "$WEB_HOST" --port "$WEB_PORT" &
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
