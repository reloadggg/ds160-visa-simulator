#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${ROOT_DIR}/.dev/logs"
API_LOG_FILE="${LOG_DIR}/api.log"
WEB_LOG_FILE="${LOG_DIR}/web.log"

mkdir -p "${LOG_DIR}"
touch "${API_LOG_FILE}" "${WEB_LOG_FILE}"

tail -n 80 -f "${API_LOG_FILE}" "${WEB_LOG_FILE}"
