#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${UI_SMOKE_HOST:-127.0.0.1}"
PORT="${UI_SMOKE_PORT:-8010}"
BASE_URL="http://${HOST}:${PORT}"
SMOKE_MESSAGE="${UI_SMOKE_MESSAGE:-My mother and father will cover all my tuition and living expenses.}"

SERVER_LOG="$(mktemp)"
SNAPSHOT_FILE="$(mktemp)"
SERVER_PID=""

cleanup() {
  local exit_code=$?

  if [[ -n "${SERVER_PID}" ]] && kill -0 "${SERVER_PID}" >/dev/null 2>&1; then
    kill "${SERVER_PID}" >/dev/null 2>&1 || true
    wait "${SERVER_PID}" 2>/dev/null || true
  fi

  agent-browser close >/dev/null 2>&1 || true

  if [[ ${exit_code} -eq 0 ]]; then
    rm -f "${SERVER_LOG}" "${SNAPSHOT_FILE}"
    return
  fi

  echo "UI smoke failed. Server log: ${SERVER_LOG}" >&2
  tail -n 200 "${SERVER_LOG}" >&2 || true
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
  local url="${BASE_URL}/healthz"
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

snapshot_to_file() {
  agent-browser snapshot -i >"${SNAPSHOT_FILE}"
}

extract_last_ref() {
  local pattern=$1

  uv run python - "${SNAPSHOT_FILE}" "${pattern}" <<'PY'
import pathlib
import re
import sys

snapshot_path = pathlib.Path(sys.argv[1])
pattern = sys.argv[2]
text = snapshot_path.read_text()
matches = re.findall(pattern, text, flags=re.MULTILINE)
if not matches:
    raise SystemExit(1)
value = matches[-1]
if isinstance(value, tuple):
    value = value[0]
print(value)
PY
}

assert_snapshot_contains() {
  local needle=$1
  if ! grep -Fq "${needle}" "${SNAPSHOT_FILE}"; then
    echo "Expected snapshot to contain: ${needle}" >&2
    return 1
  fi
}

assert_page_text_contains() {
  local ref=$1
  local needle=$2
  local page_text

  page_text="$(agent-browser get text "@${ref}")"
  if [[ "${page_text}" != *"${needle}"* ]]; then
    echo "Expected page text to contain: ${needle}" >&2
    return 1
  fi
}

require_bin uv
require_bin curl
require_bin agent-browser

cd "${ROOT_DIR}"

uv run uvicorn app.main:app --host "${HOST}" --port "${PORT}" >"${SERVER_LOG}" 2>&1 &
SERVER_PID=$!

wait_for_healthz

agent-browser open "${BASE_URL}/ui"
agent-browser wait --load networkidle
snapshot_to_file

FAMILY_REF="$(extract_last_ref 'button "F-1" \[ref=(e\d+)\]')"
agent-browser click "@${FAMILY_REF}"
agent-browser wait 3000
snapshot_to_file

assert_snapshot_contains "已创建 f1 会话。"
assert_snapshot_contains "当前建议优先准备："

TEXTBOX_REF="$(extract_last_ref 'textbox "Type your message here\.\.\." \[ref=(e\d+)\]')"
SEND_REF="$(extract_last_ref 'button \[disabled, ref=(e\d+)\]')"
ROOT_TEXT_REF="$(extract_last_ref 'generic "欢迎使用 DS-160 模拟器.*\[ref=(e\d+)\]')"
agent-browser type "@${TEXTBOX_REF}" "${SMOKE_MESSAGE}"
snapshot_to_file

agent-browser click "@${SEND_REF}"
agent-browser wait 6000
snapshot_to_file

assert_page_text_contains "${ROOT_TEXT_REF}" "Please upload funding proof."
assert_page_text_contains "${ROOT_TEXT_REF}" "当前最缺 funding_proof"

REPORT_REF="$(extract_last_ref 'button "查看用户报告" \[ref=(e\d+)\]')"
agent-browser click "@${REPORT_REF}"
agent-browser wait 3000
snapshot_to_file

assert_page_text_contains "${ROOT_TEXT_REF}" "当前结论："

echo "UI browser smoke passed against ${BASE_URL}/ui"
