#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${UI_SMOKE_HOST:-127.0.0.1}"
API_PORT="${UI_SMOKE_API_PORT:-8010}"
WEB_PORT="${UI_SMOKE_WEB_PORT:-3010}"
API_BASE_URL="http://${HOST}:${API_PORT}"
WEB_BASE_URL="http://${HOST}:${WEB_PORT}"

API_LOG="$(mktemp)"
WEB_LOG="$(mktemp)"
SNAPSHOT_FILE="$(mktemp)"
API_PID=""
WEB_PID=""

cleanup() {
  local exit_code=$?

  if [[ -n "${WEB_PID}" ]] && kill -0 "${WEB_PID}" >/dev/null 2>&1; then
    kill "${WEB_PID}" >/dev/null 2>&1 || true
    wait "${WEB_PID}" 2>/dev/null || true
  fi

  if [[ -n "${API_PID}" ]] && kill -0 "${API_PID}" >/dev/null 2>&1; then
    kill "${API_PID}" >/dev/null 2>&1 || true
    wait "${API_PID}" 2>/dev/null || true
  fi

  agent-browser close >/dev/null 2>&1 || true

  if [[ ${exit_code} -eq 0 ]]; then
    rm -f "${API_LOG}" "${WEB_LOG}" "${SNAPSHOT_FILE}"
    return
  fi

  echo "UI smoke failed. API log: ${API_LOG}" >&2
  tail -n 120 "${API_LOG}" >&2 || true
  echo "UI smoke failed. Web log: ${WEB_LOG}" >&2
  tail -n 120 "${WEB_LOG}" >&2 || true
  rm -f "${SNAPSHOT_FILE}"
}

trap cleanup EXIT

require_bin() {
  local name=$1
  if ! command -v "${name}" >/dev/null 2>&1; then
    echo "Missing required command: ${name}" >&2
    exit 1
  fi
}

wait_for_healthz() {
  local url="${API_BASE_URL}/healthz"
  local attempt

  for attempt in $(seq 1 60); do
    if curl -fsS "${url}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.5
  done

  echo "Timed out waiting for ${url}" >&2
  return 1
}

wait_for_web() {
  local attempt

  for attempt in $(seq 1 120); do
    if curl -fsS "${WEB_BASE_URL}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.5
  done

  echo "Timed out waiting for ${WEB_BASE_URL}" >&2
  return 1
}

snapshot_to_file() {
  agent-browser snapshot -i >"${SNAPSHOT_FILE}"
}

assert_snapshot_contains() {
  local needle=$1
  if ! grep -Fq "${needle}" "${SNAPSHOT_FILE}"; then
    echo "Expected snapshot to contain: ${needle}" >&2
    return 1
  fi
}

require_bin uv
require_bin pnpm
require_bin curl
require_bin agent-browser

cd "${ROOT_DIR}"

uv run uvicorn app.main:app --host "${HOST}" --port "${API_PORT}" >"${API_LOG}" 2>&1 &
API_PID=$!

wait_for_healthz

(
  cd "${ROOT_DIR}/web"
  NEXT_PUBLIC_API_BASE_URL="${API_BASE_URL}" NEXT_PUBLIC_MOCK=false pnpm dev --hostname "${HOST}" --port "${WEB_PORT}" >"${WEB_LOG}" 2>&1
) &
WEB_PID=$!

wait_for_web

agent-browser open "${WEB_BASE_URL}"
agent-browser wait --load networkidle
snapshot_to_file

assert_snapshot_contains "DS-160"
assert_snapshot_contains "面签工作台"
assert_snapshot_contains "选择签证类型"

echo "UI browser smoke passed against ${WEB_BASE_URL}"
