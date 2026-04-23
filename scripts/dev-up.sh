#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEV_DIR="${ROOT_DIR}/.dev"
LOG_DIR="${DEV_DIR}/logs"
API_PID_FILE="${DEV_DIR}/api.pid"
WEB_PID_FILE="${DEV_DIR}/web.pid"
API_LOG_FILE="${LOG_DIR}/api.log"
WEB_LOG_FILE="${LOG_DIR}/web.log"
API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-8000}"
WEB_HOST="${WEB_HOST:-127.0.0.1}"
WEB_PORT="${WEB_PORT:-3000}"

require_bin() {
  local name=$1
  if ! command -v "${name}" >/dev/null 2>&1; then
    echo "缺少命令: ${name}" >&2
    exit 1
  fi
}

port_listening() {
  local host=$1
  local port=$2

  uv run python - "${host}" "${port}" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.settimeout(0.5)
    raise SystemExit(0 if sock.connect_ex((host, port)) == 0 else 1)
PY
}

read_pid() {
  local pid_file=$1
  if [[ -f "${pid_file}" ]]; then
    tr -d '[:space:]' <"${pid_file}"
  fi
}

is_running() {
  local pid_file=$1
  local pid
  pid="$(read_pid "${pid_file}")"
  [[ -n "${pid}" ]] && kill -0 "${pid}" >/dev/null 2>&1
}

cleanup_stale_pid_file() {
  local pid_file=$1
  if [[ -f "${pid_file}" ]] && ! is_running "${pid_file}"; then
    rm -f "${pid_file}"
  fi
}

wait_for_url() {
  local url=$1
  local attempts=${2:-120}
  local delay=${3:-0.5}
  local attempt

  for attempt in $(seq 1 "${attempts}"); do
    if curl -fsS "${url}" >/dev/null 2>&1; then
      return 0
    fi
    sleep "${delay}"
  done

  return 1
}

print_log_tail() {
  local log_file=$1
  if [[ -f "${log_file}" ]]; then
    echo "----- ${log_file} -----" >&2
    tail -n 80 "${log_file}" >&2 || true
  fi
}

start_api() {
  cleanup_stale_pid_file "${API_PID_FILE}"
  if is_running "${API_PID_FILE}"; then
    echo "后端已在运行: PID $(read_pid "${API_PID_FILE}")"
    return 0
  fi

  if port_listening "${API_HOST}" "${API_PORT}"; then
    echo "后端端口 ${API_PORT} 已被占用，请先释放端口或改用 API_PORT=其它端口 make dev" >&2
    exit 1
  fi

  if [[ ! -d "${ROOT_DIR}/.venv" ]]; then
    echo "初始化后端依赖..."
    (cd "${ROOT_DIR}" && uv sync --dev)
  fi

  : >"${API_LOG_FILE}"
  setsid bash -lc "cd '${ROOT_DIR}' && exec uv run uvicorn app.main:app --host '${API_HOST}' --port '${API_PORT}' --reload" \
    >"${API_LOG_FILE}" 2>&1 &
  echo "$!" >"${API_PID_FILE}"

  if ! wait_for_url "http://${API_HOST}:${API_PORT}/healthz" 120 0.5; then
    echo "后端启动失败" >&2
    print_log_tail "${API_LOG_FILE}"
    exit 1
  fi
  if ! is_running "${API_PID_FILE}"; then
    echo "后端进程已退出" >&2
    print_log_tail "${API_LOG_FILE}"
    exit 1
  fi
}

start_web() {
  cleanup_stale_pid_file "${WEB_PID_FILE}"
  if is_running "${WEB_PID_FILE}"; then
    echo "前端已在运行: PID $(read_pid "${WEB_PID_FILE}")"
    return 0
  fi

  if port_listening "${WEB_HOST}" "${WEB_PORT}"; then
    echo "前端端口 ${WEB_PORT} 已被占用，请先释放端口或改用 WEB_PORT=其它端口 make dev" >&2
    exit 1
  fi

  if [[ ! -f "${ROOT_DIR}/web/.env.local" ]]; then
    cp "${ROOT_DIR}/web/.env.example" "${ROOT_DIR}/web/.env.local"
  fi

  if [[ ! -d "${ROOT_DIR}/web/node_modules" ]]; then
    echo "初始化前端依赖..."
    (cd "${ROOT_DIR}/web" && pnpm install)
  fi

  : >"${WEB_LOG_FILE}"
  setsid bash -lc "cd '${ROOT_DIR}/web' && exec pnpm dev --hostname '${WEB_HOST}' --port '${WEB_PORT}'" \
    >"${WEB_LOG_FILE}" 2>&1 &
  echo "$!" >"${WEB_PID_FILE}"

  if ! wait_for_url "http://${WEB_HOST}:${WEB_PORT}" 180 0.5; then
    echo "前端启动失败" >&2
    print_log_tail "${WEB_LOG_FILE}"
    exit 1
  fi
  if ! is_running "${WEB_PID_FILE}"; then
    echo "前端进程已退出" >&2
    print_log_tail "${WEB_LOG_FILE}"
    exit 1
  fi
}

require_bin uv
require_bin pnpm
require_bin curl
mkdir -p "${LOG_DIR}"

start_api
start_web

echo "后端: http://${API_HOST}:${API_PORT}"
echo "前端: http://${WEB_HOST}:${WEB_PORT}"
echo "日志: ${LOG_DIR}"
