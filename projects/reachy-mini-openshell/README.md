---
title: Reachy OpenShell
emoji: 🤖
colorFrom: purple
colorTo: gray
sdk: static
pinned: false
tags:
  - reachy_mini
  - reachy_mini_python_app
---

# Reachy OpenShell

Reachy OpenShell is a locked-profile fork of the Reachy Mini conversation app
for OpenShell research.

## Customize

The app is locked to:

```text
src/reachy_mini_conversation_app/profiles/_reachy_mini_conversation_app_locked_profile
```

That folder is the customization surface:

- `instructions.txt`: system prompt for the assistant
- `tools.txt`: explicit tool allow-list
- Python files in that folder: profile-local tools

## Run Locally

Start the Reachy Mini daemon first. For the simulator baseline:

```bash
reachy-mini-daemon --sim --scene minimal --headless --no-media --fastapi-host 127.0.0.1 --fastapi-port 8000 --dataset-update-interval 0
```

Then run the conversation app:

```bash
python -m reachy_mini_conversation_app --gradio --no-camera
```

The app also installs a console script:

```bash
reachy-mini-conversation-app --gradio --no-camera
```

Simulation mode auto-enables Gradio; `--gradio` is included here so the expected
browser interface is explicit.
