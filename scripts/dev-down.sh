#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEV_DIR="${ROOT_DIR}/.dev"
API_PID_FILE="${DEV_DIR}/api.pid"
WEB_PID_FILE="${DEV_DIR}/web.pid"

read_pid() {
  local pid_file=$1
  if [[ -f "${pid_file}" ]]; then
    tr -d '[:space:]' <"${pid_file}"
  fi
}

stop_pid_file() {
  local name=$1
  local pid_file=$2
  local pid

  pid="$(read_pid "${pid_file}")"
  if [[ -z "${pid}" ]]; then
    echo "${name}: 未运行"
    rm -f "${pid_file}"
    return 0
  fi

  if kill -0 "${pid}" >/dev/null 2>&1; then
    kill -TERM -- "-${pid}" >/dev/null 2>&1 || kill -TERM "${pid}" >/dev/null 2>&1 || true
    for _ in $(seq 1 20); do
      if ! kill -0 "${pid}" >/dev/null 2>&1; then
        break
      fi
      sleep 0.25
    done
    if kill -0 "${pid}" >/dev/null 2>&1; then
      kill -KILL -- "-${pid}" >/dev/null 2>&1 || kill -KILL "${pid}" >/dev/null 2>&1 || true
    fi
    echo "${name}: 已停止 (PID ${pid})"
  else
    echo "${name}: 进程不存在，清理 PID 文件"
  fi

  rm -f "${pid_file}"
}

stop_pid_file "后端" "${API_PID_FILE}"
stop_pid_file "前端" "${WEB_PID_FILE}"
