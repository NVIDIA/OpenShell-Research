# Reachy OpenShell

Reachy Mini conversation demo for OpenShell: Gradio UI, simulator support,
microphone or text input, Reachy movement tools, and selectable model backends.
The default and preferred starting point is OpenAI Realtime.

Commands:

- package: `reachy_mini_conversation_app`
- app: `reachy-mini-conversation-app`
- module: `python -m reachy_mini_conversation_app`
- check: `reachy-mini-backend-check`

## Quick Start

Start here: OpenAI Realtime + the Reachy Mini simulator.

Requirements:

- macOS
- Python 3.10, 3.11, or 3.12. Python 3.12 is recommended.
- `uv`
- `OPENAI_API_KEY` exported in the shell that starts the app, with access to
  the OpenAI Realtime API

From the repository root:

```bash
cd projects/reachy-mini-openshell
cp .env.example .env
export OPENAI_API_KEY=sk-...
./scripts/start-local.sh
```

The launcher creates `.venv`, runs `uv sync`, validates `.env`, starts
`reachy-mini-daemon --sim`, then prints the Gradio URL:
<http://127.0.0.1:7860/>.

Camera support follows the connected daemon. The no-media simulator starts with
camera support disabled. A real Reachy daemon that reports media available
starts the camera worker and exposes the `camera` tool to the model.

If `7860` is busy, the launcher picks the next free port through `7899`.

In the UI:

1. Use `Microphone` for voice.
2. Use `Text` for typed prompts.
3. Try: `Hi Reachy, introduce yourself and look around.`

Keep the launcher terminal open. `Ctrl+C` stops the app and the simulator it
started.

The checked-in `.env.example` already selects `BACKEND_PROVIDER=openai_realtime`.
Provider keys, base URLs, and model IDs are configured in `.env`, not in the
browser UI.

## MCP Tool Mode

MCP mode keeps the robot SDK, camera worker, and movement manager in the
separate host MCP server. The conversation process discovers hardware tools
from that server and retains only `do_nothing`, `task_status`, and
`task_cancel` locally.

Start the MCP server first:

```bash
export REACHY_MCP_TOKEN="$(cat .reachy-mcp-token)"

REACHY_MINI_SKIP_DOTENV=1 \
DAEMON_HOST=reachy-mini.local \
uv run reachy-mini-mcp-server
```

Then start the conversation app in another terminal:

```bash
export OPENAI_API_KEY=sk-...
export REACHY_MCP_TOKEN="$(cat .reachy-mcp-token)"

REACHY_TOOL_TRANSPORT=mcp \
REACHY_MCP_URL=http://127.0.0.1:8766/mcp \
uv run python -m reachy_mini_conversation_app \
  --gradio \
  --model-logs \
  --tool-transport mcp
```

This is the intermediate host test before adding the OpenShell sandbox. Camera
and scene-scan bytes are intercepted by the media processor and sent only to
the configured `VISION_*` route. Set `REQUIRE_ROUTED_VISION=1` to make missing
vision routing a startup error. If strict mode is off and vision is unavailable,
camera tools return a sanitized error and still discard the raw media rather
than falling back to the conversation model.

## Backend Selection

Set exactly one backend in `.env`:

| `BACKEND_PROVIDER` | Use when | Requires |
| --- | --- | --- |
| `openai_realtime` | First-time setup and the fastest full voice demo. | OpenAI Realtime |
| `local_stt` | Optional local ASR before Chat Completions and TTS. | Riva ASR NIM or compatible STT, Chat Completions, TTS |
| `hf_realtime` | Optional Pollen/Hugging Face realtime path. | Deployed broker or websocket |

Credentials and model routes come from `.env` or exported variables referenced
by `.env`. They are not entered in the browser UI.

### OpenAI Realtime

This is the recommended path for a first run. It handles microphone input,
assistant reasoning, Reachy tool calls, and speech output through one Realtime
session.

```bash
cp .env.example .env
export OPENAI_API_KEY=sk-...
./scripts/start-local.sh
```

```dotenv
BACKEND_PROVIDER=openai_realtime
OPENAI_REALTIME_BASE_URL=https://api.openai.com/v1
OPENAI_REALTIME_MODEL=gpt-realtime-2
OPENAI_REALTIME_VOICE=cedar
```

Leave `OPENAI_REALTIME_API_KEY` unset unless this app should use a different
key from the exported `OPENAI_API_KEY`.

### Camera model routing

Camera and scene-scan images use one model selected independently from the
Realtime voice session. The router requires the default model to be the only
entry in its server-side allowlist before any image can be uploaded:

```dotenv
VISION_BASE_URL=https://api.openai.com/v1
VISION_DEFAULT_MODEL=gpt-5.4-mini
VISION_ALLOWED_MODELS=gpt-5.4-mini
```

`VISION_API_KEY` is optional and falls back to `OPENAI_API_KEY`. The public
camera tool has no model-selection argument, so a model name in the user's
prompt cannot override `VISION_DEFAULT_MODEL`. The router sends one camera
image or up to nine ordered scene-scan frames through one Responses API request,
then returns only the text description to the Realtime conversation.

### Optional: Riva ASR NIM + Chat + TTS

Use this path after the OpenAI Realtime path is working, or when you explicitly
want microphone input transcribed by a local ASR service before text is sent
through Chat Completions and Reachy tools.

Requirement: a deployed Riva ASR NIM endpoint reachable from the app host.

The endpoint must expose:

- `GET /v1/health/ready`
- `POST /v1/audio/transcriptions`

Readiness check:

```bash
curl -X GET http://<riva-host>:9000/v1/health/ready
```

Expected response:

```json
{"status":"ready"}
```

The app uses the ASR NIM HTTP transcription route:

```text
POST http://<riva-host>:9000/v1/audio/transcriptions
```

This setup uses Riva for ASR. Speech output still uses the configured
OpenAI-compatible `TTS_*` endpoint. Riva TTS NIM exposes a different HTTP
route, `/v1/audio/synthesize`, and is not wired into this app yet.

Flow:

```text
microphone -> Riva ASR NIM -> Chat Completions + Reachy tools -> TTS -> Reachy speaks
```

```bash
cp .env.example .env
```

```dotenv
BACKEND_PROVIDER=local_stt

CHAT_API_KEY=${NVIDIA_INFERENCE_API_KEY}
CHAT_BASE_URL=https://inference-api.nvidia.com/v1
CHAT_MODEL_NAME=azure/anthropic/claude-opus-4-8

STT_API_KEY=not-needed
STT_BASE_URL=http://<riva-host>:9000/v1
STT_MODEL_NAME=parakeet-1-1b-ctc-en-us

TTS_API_KEY=${OPENAI_API_KEY}
TTS_BASE_URL=https://api.openai.com/v1
TTS_MODEL_NAME=gpt-4o-mini-tts
TTS_VOICE=cedar
```

Rules:

- `*_BASE_URL` must be plain URLs, not Markdown links.
- `*_MODEL_NAME` must match the provider's exact model ID.
- `${VAR_NAME}` values expand from the shell environment.
- Use `not-needed` for local no-auth endpoints.
- `STT_MODEL_NAME` must match an offline model ID served by the Riva ASR
  endpoint. If `stt-probe` reports `stt_model_listed=no`, use one of the model
  IDs reported by that endpoint.
- For a Whisper-compatible endpoint instead of Riva, keep the same backend and
  set `STT_BASE_URL` plus `STT_MODEL_NAME=whisper-1`.

Stage checks:

```bash
uv run reachy-mini-backend-check --live --stage stt-probe
uv run reachy-mini-backend-check --live --stage chat \
  --seed-text "Reachy, use the sweep_look tool, then tell me what you did." \
  --require-tool
uv run reachy-mini-backend-check --live --stage tts \
  --seed-text "Hello, I am Reachy."
uv run reachy-mini-backend-check --live --stage app-flow --require-tool \
  --seed-text "Reachy, use the sweep_look tool, then tell me what you did."
```

Use the fake local-STT smoke test when Riva or other real endpoints are not
ready:

```bash
scripts/smoke-local-stt.sh
```

### Optional: Hugging Face Realtime

```bash
cp .env.example .env
./scripts/start-local.sh
```

```dotenv
BACKEND_PROVIDER=hf_realtime
HF_REALTIME_CONNECTION_MODE=deployed
HF_REALTIME_MODEL=
HF_REALTIME_VOICE=Aiden
```

Leave `HF_REALTIME_MODEL` empty to use the backend default. Set `HF_TOKEN` only
when required.

For a local websocket:

```dotenv
BACKEND_PROVIDER=hf_realtime
HF_REALTIME_CONNECTION_MODE=local
HF_REALTIME_WS_URL=ws://127.0.0.1:8765/v1/realtime
```

## Verify

Config only:

```bash
uv run reachy-mini-backend-check
```

Live backend connection:

```bash
uv run reachy-mini-backend-check --live
```

Daemon status:

```text
http://127.0.0.1:8000/api/daemon/status
```

Expected fields:

```json
{
  "type": "daemon_status",
  "robot_name": "reachy_mini",
  "state": "running",
  "simulation_enabled": true,
  "no_media": true,
  "version": "1.8.0"
}
```

The full response contains more fields. `state: running` is the key signal.

## Run Commands

Launcher:

```bash
./scripts/start-local.sh
./scripts/start-local.sh --debug
./scripts/start-local.sh --model-logs
APP_PORT=7861 ./scripts/start-local.sh
REACHY_SKIP_SYNC=1 ./scripts/start-local.sh
```

Use `--model-logs` for focused INFO records containing the selected model, sanitized
requests, and response token usage/cost. Use `--debug` only when you also need the full
Realtime event stream and movement diagnostics. API keys and raw Base64 image/audio data
are redacted; media payloads are logged only by type and size.

Manual simulator, in one terminal:

```bash
uv run reachy-mini-daemon --sim --scene minimal --headless --no-media \
  --fastapi-host 127.0.0.1 --fastapi-port 8000 \
  --dataset-update-interval 0
```

Manual app, in another terminal:

```bash
uv run python -m reachy_mini_conversation_app --gradio --no-camera
```

Use a config file without replacing `.env`:

```bash
REACHY_MINI_DOTENV_PATH=path/to/alternate.env \
  uv run python -m reachy_mini_conversation_app --gradio --no-camera
```

Common app flags:

- `--gradio`: browser UI
- `--no-camera`: simulator baseline
- `--robot-name <name>`: connect to a matching daemon robot name
- `--debug`: debug logging
- `--local-vision`: local vision model; requires `local_vision`
- `--head-tracker yolo`: YOLO head tracking; requires `yolo_vision`
- `--head-tracker mediapipe`: MediaPipe head tracking; requires
  `mediapipe_vision`

## Customize Reachy

Profile files:

```text
src/reachy_mini_conversation_app/profiles/_reachy_mini_conversation_app_locked_profile
```

- `instructions.txt`: assistant behavior and personality
- `tools.txt`: allowed profile tools
- `*.py`: profile-local tool implementations

Current profile tools:

```text
dance
stop_dance
play_emotion
stop_emotion
sweep_look
camera
scan_scene
move_head
do_nothing
```

The app filters tools against runtime dependencies. In particular, `camera` and
`scan_scene` are omitted from the model session when the camera worker is
unavailable. The standalone launcher enables the camera automatically only when
the daemon reports media available.

`scan_scene` records the complete left-to-right sweep as an MP4, selects nine
chronological frames, and asks the active multimodal conversation model for one
combined account. Recordings are written to `REACHY_CAPTURE_DIR` (`./captures`
by default). For example:

```text
Scan the room, save a video, and tell me everything you saw.
```

## Optional Vision Extras

The default install includes the MuJoCo simulator backend. There are no
project-level `backend` or `sim` extras.

```bash
uv sync --extra local_vision
uv sync --extra yolo_vision
uv sync --extra mediapipe_vision
uv sync --extra all_vision
```

## Development

```bash
uv sync --group dev
uv run ruff check .
uv run ty check
uv run pytest -q
```

Useful extras:

```bash
uv run python -m compileall src tests
uv run python -m reachy_mini_conversation_app --help
uv run reachy-mini-app-assistant check .
```

`ty` is the Python type checker for this project.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `BACKEND_PROVIDER` missing | Copy `.env.example` to `.env`, then edit it. |
| App cannot connect to Reachy | Start the daemon or use `./scripts/start-local.sh`. Match `--robot-name` when using a custom daemon name. |
| OpenAI Realtime is not connected | Export `OPENAI_API_KEY` in the same shell, then run `uv run reachy-mini-backend-check --live`. |
| Local-STT text returns `404` | Use a plain `CHAT_BASE_URL` and the exact `CHAT_MODEL_NAME` accepted by the provider. |
| Riva/local microphone produces no response | Run `uv run reachy-mini-backend-check --live --stage stt-probe`; check `STT_BASE_URL` includes `/v1` and exposes `POST /audio/transcriptions`. |
| Riva ASR readiness fails | Check `http://<riva-host>:9000/v1/health/ready`, GPU/container logs, and that the app can reach the host from macOS. |
| vLLM STT says audio support is missing | Redeploy the service with vLLM audio support, then rerun `stt-probe`. |
| `uv sync` builds `pygobject` or `pycairo` on macOS | Run `uv cache clean reachy-mini pygobject pycairo`, then `uv sync`. |
| Daemon uses `--no-media` | Start the app with `--no-camera`; the launcher detects this and does so automatically. |

The checked-in uv resolution targets macOS/Darwin. For Linux deployment,
update `[tool.uv].environments` and regenerate `uv.lock`.
