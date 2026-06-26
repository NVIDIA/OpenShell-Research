# Reachy Mini Conversation Demo

This project runs a local Reachy Mini conversation demo for OpenShell. It starts
the Reachy Mini simulator, launches a Gradio browser UI, and lets you talk to
Reachy with either microphone or text input.

Source: `projects/reachy-mini-openshell`

## Quick Start

Requirements:

- macOS
- Python 3.10, 3.11, or 3.12
- `uv`
- Credentials for the backend selected in `.env`

Recommended first run: OpenAI Realtime with the Reachy Mini simulator.

```sh
cd projects/reachy-mini-openshell
cp .env.example .env
export OPENAI_API_KEY=sk-...
./scripts/start-local.sh
```

The launcher syncs dependencies, validates `.env`, starts
`reachy-mini-daemon --sim`, and prints the Gradio URL. It uses
<http://127.0.0.1:7860/> when available and picks the next free port through
`7899` when needed.

Keep the launcher terminal open. `Ctrl+C` stops the app and the simulator it
started.

## Backend Selection

Set one backend in `.env`:

| `BACKEND_PROVIDER` | Use When |
| --- | --- |
| `openai_realtime` | You want the fastest full voice demo with OpenAI Realtime. |
| `hf_realtime` | You want the Pollen/Hugging Face realtime path. |
| `local_stt` | You want microphone audio transcribed by an OpenAI-compatible STT service before Chat Completions and TTS. |

Use the single checked-in `.env.example` as the starting point:

```sh
cp .env.example .env
```

Credentials and model routes are read from `.env` and exported environment
variables referenced by `.env`. The browser UI does not accept API keys, base
URLs, or model IDs.

For OpenAI Realtime, the normal setup uses the global `OPENAI_API_KEY` exported
in the shell that starts the app:

```dotenv
BACKEND_PROVIDER=openai_realtime
OPENAI_REALTIME_BASE_URL=https://api.openai.com/v1
OPENAI_REALTIME_MODEL=gpt-realtime
OPENAI_REALTIME_VOICE=cedar
```

For local STT, the microphone path is:

```text
microphone -> STT -> Chat Completions + Reachy tools -> TTS -> Reachy speaks
```

The STT, chat, and TTS endpoints should each expose OpenAI-compatible APIs.

## What To Expect

The daemon status endpoint is:

```text
http://127.0.0.1:8000/api/daemon/status
```

A healthy simulator daemon returns JSON with these key fields:

```json
{
  "type": "daemon_status",
  "robot_name": "reachy_mini",
  "state": "running",
  "simulation_enabled": true,
  "no_media": true,
  "error": null,
  "version": "1.8.0"
}
```

The full response contains more fields. `state: "running"` is the important
signal.

Open the Gradio URL printed by the launcher. The first screen should look like
this:

![Gradio conversation UI for Talk with Reachy Mini](../assets/reachy-mini-openshell/screenshots/gradio-home.png)

Use `Microphone` for voice or `Text` for typed prompts. A good first prompt is:

```text
Hi Reachy, introduce yourself and look around.
```

Because the local simulator runs with `--no-media` and the app starts with
`--no-camera`, camera and head-tracking features are disabled. Conversation and
motion tools still work through the simulated daemon.

## Useful Checks

Validate configuration without opening the browser:

```sh
uv run reachy-mini-backend-check
```

Call the configured backend:

```sh
uv run reachy-mini-backend-check --live
```

Check local-STT stages independently:

```sh
uv run reachy-mini-backend-check --live --stage stt-probe
uv run reachy-mini-backend-check --live --stage chat \
  --seed-text "Reachy, use the sweep_look tool, then tell me what you did." \
  --require-tool
uv run reachy-mini-backend-check --live --stage tts \
  --seed-text "Hello, I am Reachy."
```

Run the fake local-STT smoke workflow when external STT or TTS services are not
ready:

```sh
scripts/smoke-local-stt.sh
```

## Manual Startup

Use the launcher for normal development. Manual startup is useful when you need
separate daemon and app terminals.

Simulator terminal:

```sh
uv run reachy-mini-daemon --sim --scene minimal --headless --no-media \
  --fastapi-host 127.0.0.1 --fastapi-port 8000 \
  --dataset-update-interval 0
```

App terminal:

```sh
uv run python -m reachy_mini_conversation_app --gradio --no-camera
```

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `BACKEND_PROVIDER` missing | Copy `.env.example` to `.env`, then edit `.env`. |
| App cannot connect to Reachy | Use `./scripts/start-local.sh`, or verify the daemon status endpoint reports `state: "running"`. |
| OpenAI Realtime is not connected | Export `OPENAI_API_KEY` in the same shell that starts the app, then run `uv run reachy-mini-backend-check --live`. |
| Local-STT text returns `404` | Use a plain `CHAT_BASE_URL` ending in `/v1` when required, and set `CHAT_MODEL_NAME` to the provider's exact model ID. |
| Local-STT microphone produces no response | Run the `stt-probe` check and confirm `STT_BASE_URL` exposes `POST /audio/transcriptions`. |
| Daemon reports `MuJoCo is not installed` | Run `uv sync` from `projects/reachy-mini-openshell`, then start the daemon through `uv run` or the launcher. |
