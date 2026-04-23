#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEV_DIR="${ROOT_DIR}/.dev"
LOG_DIR="${DEV_DIR}/logs"
API_PID_FILE="${DEV_DIR}/api.pid"
WEB_PID_FILE="${DEV_DIR}/web.pid"
API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-8000}"
WEB_HOST="${WEB_HOST:-127.0.0.1}"
WEB_PORT="${WEB_PORT:-3000}"

read_pid() {
  local pid_file=$1
  if [[ -f "${pid_file}" ]]; then
    tr -d '[:space:]' <"${pid_file}"
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

port_owner_pid() {
  local port=$1
  ss -ltnp "( sport = :${port} )" 2>/dev/null | sed -n 's/.*pid=\([0-9]\+\).*/\1/p' | head -n 1
}

describe_service() {
  local name=$1
  local pid_file=$2
  local url=$3
  local host=$4
  local port=$5
  local pid
  local owner_pid

  pid="$(read_pid "${pid_file}")"
  if [[ -n "${pid}" ]] && kill -0 "${pid}" >/dev/null 2>&1; then
    echo "${name}: 运行中 (PID ${pid}) ${url}"
    return 0
  fi

  owner_pid="$(port_owner_pid "${port}")"
  if port_listening "${host}" "${port}"; then
    if [[ -n "${owner_pid}" ]]; then
      echo "${name}: 端口已被外部进程占用 (PID ${owner_pid}) ${url}"
    else
      echo "${name}: 端口已被外部进程占用 ${url}"
    fi
    return 0
  fi

  echo "${name}: 未运行 ${url}"
}

describe_service "后端" "${API_PID_FILE}" "http://${API_HOST}:${API_PORT}" "${API_HOST}" "${API_PORT}"
describe_service "前端" "${WEB_PID_FILE}" "http://${WEB_HOST}:${WEB_PORT}" "${WEB_HOST}" "${WEB_PORT}"
echo "日志目录: ${LOG_DIR}"
