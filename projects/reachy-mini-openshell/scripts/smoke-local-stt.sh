#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

HOST="${SMOKE_BACKEND_HOST:-127.0.0.1}"
PORT="${SMOKE_BACKEND_PORT:-8766}"
REAL_REACHY="${SMOKE_REAL_REACHY:-0}"
GRADIO_SMOKE="${SMOKE_GRADIO:-0}"
APP_HOST="${SMOKE_APP_HOST:-127.0.0.1}"
APP_PORT="${SMOKE_APP_PORT:-7897}"
ROBOT_NAME="${SMOKE_ROBOT_NAME:-}"
SEED_TEXT="${SMOKE_SEED_TEXT:-Reachy, use the sweep_look tool, then tell me what you did.}"
STARTUP_TIMEOUT="${SMOKE_BACKEND_TIMEOUT:-10}"
APP_STARTUP_TIMEOUT="${SMOKE_APP_TIMEOUT:-20}"
SHUTDOWN_TIMEOUT="${SMOKE_SHUTDOWN_TIMEOUT:-25}"

SERVER_PID=""
APP_PID=""
SMOKE_ENV=""

usage() {
  cat <<'EOF'
Usage: scripts/smoke-local-stt.sh [--real-reachy] [--gradio] [--robot-name NAME]

Runs a local OpenAI-compatible fake STT/chat/TTS backend, writes a temporary
local_stt config, and checks the app's microphone app-flow path.

Options:
  --real-reachy       Also run the app-flow check through the real Reachy SDK
                      and movement manager. Requires a running daemon/simulator.
  --gradio            Also launch the Gradio app, verify the UI labels, and send
                      a text tool-call prompt through the running app. Requires
                      a running daemon/simulator.
  --robot-name NAME   Robot name to pass to reachy-mini-backend-check with
                      --real-reachy and to the Gradio app smoke when provided.
  -h, --help          Show this help.

Environment:
  SMOKE_BACKEND_HOST       Fake backend host. Default: 127.0.0.1
  SMOKE_BACKEND_PORT       Fake backend port. Default: 8766
  SMOKE_APP_HOST           Gradio app host for --gradio. Default: 127.0.0.1
  SMOKE_APP_PORT           Gradio app port for --gradio. Default: 7897
  SMOKE_SEED_TEXT          Seed text for the generated input speech.
  SMOKE_BACKEND_TIMEOUT    Seconds to wait for the fake backend. Default: 10
  SMOKE_APP_TIMEOUT        Seconds to wait for the Gradio app. Default: 20
  SMOKE_SHUTDOWN_TIMEOUT   Seconds to wait before forcing shutdown. Default: 25
EOF
}

log() {
  printf '[reachy-smoke] %s\n' "$*"
}

terminate_process_tree() {
  local pid="$1"
  local signal="$2"
  local children child

  children="$(pgrep -P "${pid}" 2>/dev/null || true)"
  for child in ${children}; do
    terminate_process_tree "${child}" "${signal}"
  done
  kill "-${signal}" "${pid}" >/dev/null 2>&1 || true
}

wait_for_process_exit() {
  local pid="$1"
  local timeout_seconds="$2"
  local deadline=$((SECONDS + timeout_seconds))

  while kill -0 "${pid}" >/dev/null 2>&1; do
    if (( SECONDS >= deadline )); then
      return 1
    fi
    sleep 0.2
  done
  return 0
}

stop_process_tree() {
  local pid="$1"
  local label="$2"
  if [[ -z "${pid}" ]] || ! kill -0 "${pid}" >/dev/null 2>&1; then
    return
  fi

  terminate_process_tree "${pid}" INT
  if ! wait_for_process_exit "${pid}" "${SHUTDOWN_TIMEOUT}"; then
    log "Forcing ${label} shutdown"
    terminate_process_tree "${pid}" TERM
    if ! wait_for_process_exit "${pid}" 3; then
      terminate_process_tree "${pid}" KILL
    fi
  fi
  wait "${pid}" >/dev/null 2>&1 || true
}

cleanup() {
  stop_process_tree "${APP_PID}" "Gradio app"
  stop_process_tree "${SERVER_PID}" "fake backend"
  if [[ -n "${SMOKE_ENV}" ]]; then
    rm -f "${SMOKE_ENV}"
  fi
}
trap cleanup EXIT INT TERM

while [[ $# -gt 0 ]]; do
  case "$1" in
    --real-reachy)
      REAL_REACHY=1
      shift
      ;;
    --gradio)
      GRADIO_SMOKE=1
      shift
      ;;
    --robot-name)
      if [[ $# -lt 2 ]]; then
        printf 'Missing value for --robot-name\n' >&2
        exit 2
      fi
      ROBOT_NAME="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown argument: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

cd "${PROJECT_DIR}"

if ! command -v uv >/dev/null 2>&1; then
  printf 'Missing required command: uv\n' >&2
  exit 1
fi

ensure_url_ready() {
  local url="$1"
  local timeout_seconds="$2"
  local label="$3"

  local deadline=$((SECONDS + timeout_seconds))
  until uv run python - "${url}" <<'PY' >/dev/null 2>&1
import sys
import urllib.request

url = sys.argv[1]
try:
    with urllib.request.urlopen(url, timeout=0.5) as response:
        raise SystemExit(0 if 200 <= response.status < 300 else 1)
except Exception:
    raise SystemExit(1)
PY
  do
    if (( SECONDS >= deadline )); then
      printf '%s did not become ready within %s seconds.\n' "${label}" "${timeout_seconds}" >&2
      exit 1
    fi
    sleep 0.25
  done
}

SMOKE_ENV="$(mktemp "${TMPDIR:-/tmp}/reachy-local-stt-smoke.XXXXXX")"
cat >"${SMOKE_ENV}" <<EOF
BACKEND_PROVIDER=local_stt
CHAT_API_KEY=not-needed
CHAT_BASE_URL=http://${HOST}:${PORT}/v1
CHAT_MODEL_NAME=fake-chat
STT_API_KEY=not-needed
STT_BASE_URL=http://${HOST}:${PORT}/v1
STT_MODEL_NAME=fake-whisper
TTS_API_KEY=not-needed
TTS_BASE_URL=http://${HOST}:${PORT}/v1
TTS_MODEL_NAME=fake-tts
TTS_VOICE=fake-voice
MIC_TRANSCRIPTION_RMS_THRESHOLD=100
MIC_TRANSCRIPTION_MIN_AUDIO_MS=100
MIC_TRANSCRIPTION_SILENCE_MS=200
MIC_TRANSCRIPTION_MAX_AUDIO_MS=5000
EOF

log "Starting fake OpenAI-compatible backend at http://${HOST}:${PORT}/v1"
uv run python scripts/fake_openai_backend.py --host "${HOST}" --port "${PORT}" &
SERVER_PID="$!"

log "Waiting for fake backend"
ensure_url_ready "http://${HOST}:${PORT}/v1/models" "${STARTUP_TIMEOUT}" "Fake backend"

log "Running local_stt app-flow smoke with fake Reachy dependencies"
uv run reachy-mini-backend-check \
  --env-file "${SMOKE_ENV}" \
  --live \
  --require-tool \
  --seed-text "${SEED_TEXT}"

if [[ "${REAL_REACHY}" == "1" ]]; then
  real_args=(
    --env-file "${SMOKE_ENV}"
    --live
    --require-tool
    --real-reachy
    --seed-text "${SEED_TEXT}"
  )
  if [[ -n "${ROBOT_NAME}" ]]; then
    real_args+=(--robot-name "${ROBOT_NAME}")
  fi

  log "Running local_stt app-flow smoke with real Reachy dependencies"
  uv run reachy-mini-backend-check "${real_args[@]}"
fi

if [[ "${GRADIO_SMOKE}" == "1" ]]; then
  app_args=(--gradio --no-camera)
  if [[ -n "${ROBOT_NAME}" ]]; then
    app_args+=(--robot-name "${ROBOT_NAME}")
  fi

  log "Starting Gradio app at http://${APP_HOST}:${APP_PORT}/"
  REACHY_MINI_DOTENV_PATH="${SMOKE_ENV}" \
    GRADIO_SERVER_NAME="${APP_HOST}" \
    GRADIO_SERVER_PORT="${APP_PORT}" \
    uv run python -m reachy_mini_conversation_app "${app_args[@]}" &
  APP_PID="$!"

  log "Waiting for Gradio app"
  ensure_url_ready "http://${APP_HOST}:${APP_PORT}/" "${APP_STARTUP_TIMEOUT}" "Gradio app"

  log "Running Gradio UI/text smoke"
uv run python - "${APP_HOST}" "${APP_PORT}" "${SEED_TEXT}" <<'PY'
import json
import sys
import urllib.request

from gradio_client import Client

host, port, seed_text = sys.argv[1], sys.argv[2], sys.argv[3]
base_url = f"http://{host}:{port}/"

with urllib.request.urlopen(base_url, timeout=5.0) as response:
    html = response.read().decode("utf-8", errors="replace")

for expected in ("Talk with Reachy Mini", "Microphone", "Text"):
    if expected not in html:
        raise SystemExit(f"Missing expected Gradio UI label: {expected}")

with urllib.request.urlopen(f"{base_url}config", timeout=5.0) as response:
    config = json.loads(response.read().decode("utf-8"))

components = config.get("components", [])
component_text = json.dumps(components).lower()
if "webrtc" not in component_text:
    raise SystemExit("Gradio config does not expose the WebRTC microphone component")

input_components = [
    component
    for component in components
    if component.get("props", {}).get("label") == "Input"
]
if not input_components:
    raise SystemExit("Gradio config is missing the Input mode selector")

input_choices = input_components[0].get("props", {}).get("choices", [])
if "Microphone" not in str(input_choices) or "Text" not in str(input_choices):
    raise SystemExit(f"Input mode selector is missing Microphone/Text choices: {input_choices!r}")

client = Client(base_url)
messages, cleared_text = client.predict(seed_text, [], api_name="/send_text_message")
titles = [
    message.get("metadata", {}).get("title")
    for message in messages
    if isinstance(message, dict) and isinstance(message.get("metadata"), dict)
]
contents = [
    message.get("content")
    for message in messages
    if isinstance(message, dict) and isinstance(message.get("content"), str)
]
if "Used tool sweep_look" not in titles:
    raise SystemExit(f"Gradio text smoke did not use sweep_look: titles={titles!r}")
if "I swept my gaze and returned to center." not in contents:
    raise SystemExit(f"Gradio text smoke did not return the fake assistant response: contents={contents!r}")
if cleared_text != "":
    raise SystemExit(f"Expected text input to be cleared, got: {cleared_text!r}")

print("gradio_ui=ok")
print("gradio_text_tool=Used tool sweep_look")
print("gradio_text_assistant=I swept my gaze and returned to center.")
PY
fi

log "Smoke check complete"
