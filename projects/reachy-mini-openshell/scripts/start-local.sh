#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
LAUNCHER_PATH="${BASH_SOURCE[0]}"

cd "${PROJECT_DIR}"

APP_HOST="${APP_HOST:-127.0.0.1}"
APP_PORT="${APP_PORT:-}"
APP_PORT_START="${APP_PORT_START:-7860}"
APP_PORT_END="${APP_PORT_END:-7899}"
DAEMON_HOST="${DAEMON_HOST:-127.0.0.1}"
DAEMON_PORT="${DAEMON_PORT:-8000}"
PYTHON_VERSION="${PYTHON_VERSION:-3.12}"
REACHY_SKIP_SYNC="${REACHY_SKIP_SYNC:-0}"
REACHY_DAEMON_TIMEOUT="${REACHY_DAEMON_TIMEOUT:-45}"

DAEMON_PID=""

log() {
  printf '[reachy-start] %s\n' "$*"
}

finish() {
  if [[ -n "${DAEMON_PID}" ]]; then
    log "Stopping simulator daemon pid=${DAEMON_PID}"
    kill "${DAEMON_PID}" >/dev/null 2>&1 || true
    wait "${DAEMON_PID}" >/dev/null 2>&1 || true
  fi
}
trap finish EXIT INT TERM

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf 'Missing required command: %s\n' "$1" >&2
    exit 1
  fi
}

daemon_status_ok() {
  "${PROJECT_DIR}/.venv/bin/python" - "$DAEMON_HOST" "$DAEMON_PORT" <<'PY' >/dev/null 2>&1
import sys
import urllib.request

host, port = sys.argv[1], sys.argv[2]
try:
    with urllib.request.urlopen(f"http://{host}:{port}/api/daemon/status", timeout=1.5) as response:
        raise SystemExit(0 if 200 <= response.status < 500 else 1)
except Exception:
    raise SystemExit(1)
PY
}

pick_app_port() {
  if [[ -n "${APP_PORT}" ]]; then
    "${PROJECT_DIR}/.venv/bin/python" - "$APP_HOST" "$APP_PORT" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.settimeout(0.2)
    if sock.connect_ex((host, port)) == 0:
        raise SystemExit(f"Requested APP_PORT={port} is already in use")
PY
    printf '%s\n' "${APP_PORT}"
    return
  fi

  "${PROJECT_DIR}/.venv/bin/python" - "$APP_HOST" "$APP_PORT_START" "$APP_PORT_END" <<'PY'
import socket
import sys

host = sys.argv[1]
start = int(sys.argv[2])
end = int(sys.argv[3])

for port in range(start, end + 1):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        if sock.connect_ex((host, port)) != 0:
            print(port)
            raise SystemExit(0)

raise SystemExit(f"No free app port found in {start}-{end}")
PY
}

require_command uv

if [[ ! -x "${PROJECT_DIR}/.venv/bin/python" ]]; then
  log "Creating virtual environment with Python ${PYTHON_VERSION}"
  uv venv --python "${PYTHON_VERSION}"
fi

if [[ "${REACHY_SKIP_SYNC}" != "1" ]]; then
  log "Installing project dependencies with uv sync"
  if ! uv sync; then
    cat >&2 <<'EOF'

uv sync failed. This project currently has a platform-specific uv.lock.
After installing dependencies another way, rerun with:

  REACHY_SKIP_SYNC=1 ${LAUNCHER_PATH}

EOF
    exit 1
  fi
fi

if [[ ! -f ".env" ]]; then
  cp .env.example .env
  log "Created .env from .env.example. Edit it if you need different provider credentials."
fi

export PATH="${PROJECT_DIR}/.venv/bin:${PATH}"

log "Validating conversation backend configuration"
reachy-mini-backend-check

if daemon_status_ok; then
  log "Using existing Reachy daemon at http://${DAEMON_HOST}:${DAEMON_PORT}"
else
  log "Starting Reachy simulator daemon at http://${DAEMON_HOST}:${DAEMON_PORT}"
  reachy-mini-daemon \
    --sim \
    --scene minimal \
    --headless \
    --no-media \
    --fastapi-host "${DAEMON_HOST}" \
    --fastapi-port "${DAEMON_PORT}" \
    --dataset-update-interval 0 &
  DAEMON_PID="$!"

  log "Waiting for simulator daemon"
  deadline=$((SECONDS + REACHY_DAEMON_TIMEOUT))
  until daemon_status_ok; do
    if (( SECONDS >= deadline )); then
      printf 'Reachy simulator daemon did not become ready within %s seconds.\n' "${REACHY_DAEMON_TIMEOUT}" >&2
      exit 1
    fi
    sleep 1
  done
fi

SELECTED_APP_PORT="$(pick_app_port)"
export GRADIO_SERVER_NAME="${APP_HOST}"
export GRADIO_SERVER_PORT="${SELECTED_APP_PORT}"

log "Starting Reachy conversation app"
log "Open: http://${APP_HOST}:${SELECTED_APP_PORT}/"
"${PROJECT_DIR}/.venv/bin/python" -m reachy_mini_conversation_app --gradio --no-camera "$@"
