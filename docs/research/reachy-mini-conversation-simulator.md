# Reachy Mini Conversation App Simulator Tutorial

This tutorial walks through the local simulator workflow for the Reachy
OpenShell conversation app. It starts the Reachy Mini daemon in MuJoCo
simulation mode, verifies that the daemon is healthy, launches the Gradio
conversation UI, and shows what to expect in the browser.

The app in this repository is an OpenShell-oriented fork of the Reachy Mini
conversation app template. The upstream Hugging Face Space is a useful reference
for the broader app family: [pollen-robotics/reachy_mini_conversation_app](https://huggingface.co/spaces/pollen-robotics/reachy_mini_conversation_app/tree/main).

## What You Need

- macOS with Python 3.10, 3.11, or 3.12. Python 3.12 is the recommended local
  version.
- `uv`.
- Provider credentials for the backend you select in `.env`. The app supports
  OpenAI Realtime, the default Pollen/Hugging Face realtime path, and a local
  STT cascade that combines an OpenAI-compatible transcription endpoint, Chat
  Completions, and TTS.
- This repository checked out locally.

The local dependency set includes `reachy-mini[mujoco]==1.8.0`, so the MuJoCo
simulator backend is installed by the default project sync.

## 1. Install The Project

From the repository root:

```sh
cd projects/reachy-mini-openshell
uv venv --python 3.12
source .venv/bin/activate
uv sync
```

Choose one backend starter and copy it to `.env`:

```sh
cp .env.openai-realtime.example .env
# or: cp .env.hf-realtime.example .env
# or: cp .env.local-stt.example .env
```

`.env.example` is the combined local-STT starter used by the launcher when no
`.env` exists.

For OpenAI Realtime microphone and text input, use:

```sh
BACKEND_PROVIDER=openai_realtime
OPENAI_REALTIME_BASE_URL=https://api.openai.com/v1
OPENAI_REALTIME_MODEL=gpt-realtime
OPENAI_REALTIME_VOICE=cedar
```

OpenAI Realtime uses the standard, globally exported `OPENAI_API_KEY` directly.
Do not copy the global key into `.env` for the normal OpenAI path. Start the app
from a shell where `OPENAI_API_KEY` is already exported. Set
`OPENAI_REALTIME_API_KEY` in `.env` only when this app should use a different
key from the global OpenAI key.

For the default Pollen/Hugging Face realtime path, use:

```sh
BACKEND_PROVIDER=hf_realtime
HF_REALTIME_CONNECTION_MODE=deployed
HF_REALTIME_MODEL=
HF_REALTIME_VOICE=Aiden
```

For a local Hugging Face realtime backend, point at the backend websocket:

```sh
BACKEND_PROVIDER=hf_realtime
HF_REALTIME_CONNECTION_MODE=local
HF_REALTIME_WS_URL=ws://127.0.0.1:8765/v1/realtime
```

For a local STT cascade, combine an OpenAI-compatible transcription endpoint, a
Chat Completions model, and a TTS endpoint:

```sh
BACKEND_PROVIDER=local_stt
CHAT_API_KEY=${NVIDIA_INFERENCE_API_KEY}
CHAT_BASE_URL=https://inference-api.nvidia.com/v1
CHAT_MODEL_NAME=azure/anthropic/claude-opus-4-8
STT_API_KEY=not-needed
STT_BASE_URL=http://127.0.0.1:9000/v1
STT_MODEL_NAME=whisper-1
TTS_API_KEY=${OPENAI_API_KEY}
TTS_BASE_URL=https://api.openai.com/v1
TTS_MODEL_NAME=gpt-4o-mini-tts
TTS_VOICE=cedar
```

Use the exact model ID exposed by your provider. For example, NVIDIA model IDs
can look like `nvidia/nemotron-3-super-120b-a12b` or
`azure/anthropic/claude-opus-4-8`, depending on the endpoint and account.
The API key values can reference exported system environment variables using
dotenv syntax, for example `${NVIDIA_INFERENCE_API_KEY}`.

Backend routing and model selection are loaded from `.env`. Secret values can
be literal dotenv values, dotenv references to exported variables, or exported
`OPENAI_API_KEY` for OpenAI Realtime. The standard OpenAI Realtime path uses
the global `OPENAI_API_KEY` directly. The browser UI does not include fields
for credentials, endpoint routing, or model selection.

`BACKEND_PROVIDER` must be set exactly to `openai_realtime`, `hf_realtime`, or
`local_stt`. The app does not choose a backend implicitly; missing or unknown
values fail preflight and startup.

Before launching the UI, validate the selected backend:

```sh
reachy-mini-backend-check
```

To call configured backend endpoints without opening the browser, add `--live`:

```sh
reachy-mini-backend-check --live
```

For `BACKEND_PROVIDER=openai_realtime` and `BACKEND_PROVIDER=hf_realtime`, this
opens a realtime session, sends the app's session configuration, and then shuts
the session down. For `BACKEND_PROVIDER=local_stt`, `--live` defaults to the
app-flow check: it feeds audio through the handler's microphone receive path
against fake Reachy dependencies, then verifies
`mic frame handling -> STT -> Chat Completions -> Reachy tools -> TTS`.

For `BACKEND_PROVIDER=local_stt`, you can also name the app-flow stage
explicitly:

```sh
reachy-mini-backend-check --live --stage app-flow \
  --seed-text "Reachy, use the sweep_look tool, then tell me what you did."
```

The local-STT app-flow check uses fake Reachy tool dependencies by default.
After the simulator daemon is running, add `--real-reachy` to connect through
the Reachy SDK and queue movements through the real movement manager:

```sh
reachy-mini-backend-check --live --stage app-flow --require-tool --real-reachy \
  --seed-text "Reachy, use the sweep_look tool, then tell me what you did."
```

If your real STT endpoint is not reachable yet, start the local fake
OpenAI-compatible smoke workflow:

```sh
scripts/smoke-local-stt.sh
scripts/smoke-local-stt.sh --real-reachy
scripts/smoke-local-stt.sh --gradio
```

The first command checks the app handler path against fake Reachy dependencies.
The second also connects through the Reachy SDK and movement manager, so it
requires the simulator daemon to be running. The `--gradio` variant launches
the actual Gradio app, verifies the `Talk with Reachy Mini` UI, and sends a
text tool-call prompt through the running app process.

For manual debugging, you can run the fake backend directly:

```sh
uv run python scripts/fake_openai_backend.py
```

In another terminal, point a disposable config at it and run the same app-flow
check:

```sh
cat > .env.smoke <<'EOF'
BACKEND_PROVIDER=local_stt
CHAT_API_KEY=not-needed
CHAT_BASE_URL=http://127.0.0.1:8766/v1
CHAT_MODEL_NAME=fake-chat
STT_API_KEY=not-needed
STT_BASE_URL=http://127.0.0.1:8766/v1
STT_MODEL_NAME=fake-whisper
TTS_API_KEY=not-needed
TTS_BASE_URL=http://127.0.0.1:8766/v1
TTS_MODEL_NAME=fake-tts
TTS_VOICE=fake-voice
EOF

reachy-mini-backend-check --env-file .env.smoke --live --stage app-flow --require-tool
reachy-mini-backend-check --env-file .env.smoke --live --stage app-flow --require-tool --real-reachy
```

This checks the app's OpenAI-compatible HTTP plumbing and simulator movement
path. It does not replace a final check against the real STT service.

While services are coming online, check each local STT stage independently:

```sh
reachy-mini-backend-check --live --stage stt --audio-file ./sample-input.wav
reachy-mini-backend-check --live --stage chat \
  --seed-text "Reachy, use the sweep_look tool, then tell me what you did." \
  --require-tool
reachy-mini-backend-check --live --stage tts \
  --seed-text "Hello, I am Reachy."
```

`--stage stt` only needs `STT_*` values and a sample audio file.
`--stage chat` only needs `CHAT_*` values. `--stage tts` only needs `TTS_*`
values. The full `--stage app-flow` check still requires all local-STT pieces
and feeds audio through the handler's microphone receive path before STT.
Use `--require-tool` with `--stage chat` or `--stage app-flow` when the check
should fail unless the model calls a Reachy tool.

To try a candidate dotenv file without replacing `.env`, pass it explicitly to
the checker:

```sh
reachy-mini-backend-check --env-file .env.local-stt
```

Launch the app with that same candidate file by setting
`REACHY_MINI_DOTENV_PATH`:

```sh
REACHY_MINI_DOTENV_PATH=.env.local-stt \
  python -m reachy_mini_conversation_app --gradio --no-camera
```

Run a quick import check:

```sh
python - <<'PY'
import mujoco
import reachy_mini

print("mujoco", mujoco.__version__)
print("reachy_mini", reachy_mini.__version__)
PY
```

You should see MuJoCo and Reachy Mini versions printed. If MuJoCo is missing,
rerun `uv sync` from `projects/reachy-mini-openshell`.

## 2. Start The Simulator Daemon

Start the Reachy Mini daemon in one terminal:

```sh
reachy-mini-daemon \
  --sim \
  --scene minimal \
  --headless \
  --no-media \
  --fastapi-host 127.0.0.1 \
  --fastapi-port 8000 \
  --dataset-update-interval 0
```

The important log line is:

```text
Daemon started successfully.
```

The daemon should keep running in that terminal. Leave it open while you use the
conversation app.

## 3. Verify The Daemon

Open this URL in your browser:

```text
http://127.0.0.1:8000/api/daemon/status
```

You should see syntax-highlighted JSON in the browser. The exact `version` can
vary, but a healthy simulator daemon should look like this:

```json
{
  "type": "daemon_status",
  "robot_name": "reachy_mini",
  "state": "running",
  "wireless_version": false,
  "desktop_app_daemon": false,
  "simulation_enabled": true,
  "mockup_sim_enabled": false,
  "no_media": true,
  "media_released": false,
  "camera_specs_name": "",
  "backend_status": {
    "motor_control_mode": "enabled",
    "error": null
  },
  "error": null,
  "wlan_ip": null,
  "version": "1.8.0",
  "hardware_id": null
}
```

If `state` is `error` and the message mentions MuJoCo, the simulator dependency
is missing from the environment that started the daemon. Stop the daemon, run
`uv sync`, and start it again from the activated project environment.

## 4. Start The Conversation App

Open a second terminal, activate the same environment, and start the app:

```sh
cd projects/reachy-mini-openshell
source .venv/bin/activate
python -m reachy_mini_conversation_app --gradio --no-camera
```

The console script is equivalent:

```sh
reachy-mini-conversation-app --gradio --no-camera
```

The app connects to the daemon through the Reachy Mini SDK. In simulator mode it
uses the browser-based Gradio UI. The expected launch line is:

```text
Running on local URL:  http://127.0.0.1:7860
```

If port `7860` is busy, Gradio may choose another local port. Use the URL printed
by the terminal.

## 5. Open The Gradio UI

Open the Gradio URL, usually:

```text
http://127.0.0.1:7860/
```

You should see the `Talk with Reachy Mini` page with an empty chat transcript,
an audio stream panel, and an `Input` selector.

![Gradio conversation UI for Talk with Reachy Mini](../assets/reachy-mini-openshell/screenshots/gradio-home.png)

If the app logs a missing local backend value such as `CHAT_API_KEY`,
`STT_BASE_URL`, or `TTS_BASE_URL`, stop the app, update `.env`, and restart it.
Credentials are intentionally not entered in the browser. For OpenAI Realtime,
export the standard `OPENAI_API_KEY` in the shell that starts the app. Set
`OPENAI_REALTIME_API_KEY` in `.env` only if this app should use a different
OpenAI key. If `.env` references `${NVIDIA_INFERENCE_API_KEY}`, make sure that
variable is exported in the shell that starts the app.

## 6. Talk To Reachy

Use `Microphone` mode to speak. With `BACKEND_PROVIDER=openai_realtime` or
`BACKEND_PROVIDER=hf_realtime`, browser audio streams to the selected realtime
backend. With `BACKEND_PROVIDER=local_stt`, the app buffers each spoken phrase,
sends it to `STT_BASE_URL` for transcription, sends the transcript through Chat
Completions, then plays Reachy's synthesized response from `TTS_BASE_URL`.
Click `Click to Access Microphone` in the `Stream` panel and allow microphone
access in the browser. Once the stream starts, speak naturally.

Use `Text` mode to type instead. Switch `Input` to `Text`, enter a message, and
send it from the text composer. Tool calls are supported in text mode and in
microphone-to-text mode, including providers that require the tool schema to
remain attached after a tool result is returned.

Useful first prompts are:

```text
Hello Reachy, introduce yourself.
Can you dance?
Look around the room.
Show me a happy emotion.
Stop moving.
```

Because this tutorial runs with `--no-camera`, camera and head-tracking features
are disabled. Voice conversation and motion tools are still available through the
simulated daemon.

## 7. Stop The Demo

Stop the conversation app with `Ctrl+C` in the app terminal. Then stop the
daemon with `Ctrl+C` in the daemon terminal.

## Troubleshooting

If the app cannot connect to the daemon, make sure the daemon terminal is still
running and that its status page reports `state: "running"`.

If the app starts but the browser does not open automatically, copy the `Running
on local URL` value from the terminal into your browser.

If the browser cannot access the microphone, check the browser permission prompt
and macOS microphone privacy settings.

If `reachy-mini-backend-check` reports older config keys such as
`OPENAI_BASE_URL` or `MODEL_NAME`, replace that `.env` shape with one of the
current `BACKEND_PROVIDER` configurations. Those old keys are intentionally not
used.

If text mode returns `404 page not found` with `BACKEND_PROVIDER=local_stt`,
check that `CHAT_BASE_URL` is a plain OpenAI-compatible endpoint string, such as
`https://inference-api.nvidia.com/v1`, and that `CHAT_MODEL_NAME` is an exact
model ID from that provider.

If text mode returns `401 Unauthorized` with NVIDIA endpoints, make sure
`CHAT_API_KEY=${NVIDIA_INFERENCE_API_KEY}` resolves to a key that is authorized
for generation, not only model listing.

If microphone-to-text returns a transcription error with
`BACKEND_PROVIDER=local_stt`, make sure `STT_BASE_URL` includes the `/v1` base
path expected by OpenAI-compatible clients and that the service exposes
`POST /audio/transcriptions` for `STT_MODEL_NAME`.

If the daemon reports `MuJoCo is not installed`, make sure you started the
daemon from this project environment after running `uv sync`. The dependency is
provided by `reachy-mini[mujoco]==1.8.0`.
