# Reachy Mini OpenShell Controller

This is the small trusted Reachy App that owns the robot microphone, speaker,
and camera; starts and stops the conversation process inside the existing
`reachy-agent` OpenShell sandbox; bridges PCM audio over a loopback WebSocket;
and exposes one bounded snapshot endpoint to the sandbox.

It intentionally contains no model client, tool implementation, or robot motion
call. Model-driven requests remain inside the OpenShell sandbox and reach the
robot only through policy-controlled REST requests.

The trusted camera surface is exactly `POST /camera/capture` on port `8042`.
It accepts no request arguments, does not write a file, returns one JPEG no
larger than 2 MiB, permits one capture per second, and rejects concurrent
captures. The Reachy App process—not the sandbox—owns the camera SDK object.

## Runtime contract

Before this app starts, the Reachy host must already have:

- a working `openshell` CLI and local gateway;
- a `Ready` sandbox named `reachy-agent`;
- `/opt/venv/bin/reachy-agent-control` in that sandbox image; and
- the motion-disabled policy attached to the sandbox.

Start performs only these fixed actions:

1. `openshell sandbox get reachy-agent`
2. `openshell sandbox exec ... reachy-agent-control start`
3. `openshell service get reachy-agent audio`
4. `openshell service expose reachy-agent 8765 audio` when missing, after the listener is live
5. connect robot audio to the loopback WebSocket service
6. serve the fixed camera capture route for the Reachy App lifetime

The lifecycle command verifies readiness by inspecting the sandbox's Linux TCP
listener table. It does not make an HTTP request to `127.0.0.1`, because
OpenShell intentionally blocks sandbox egress to loopback even though the
gateway can forward an explicitly exposed service to a loopback listener.
The sandbox configuration gives the first cold start 120 seconds; measured
startup on the Reachy Mini onboard Raspberry Pi is approximately 40 seconds.

Stop closes robot media and the camera adapter, then invokes
`reachy-agent-control stop`. It never
creates or deletes a sandbox and never accepts a command from the model.

## Configuration

Defaults are suitable for the documented onboard deployment. Supported
overrides are:

| Variable | Default |
| --- | --- |
| `REACHY_OPENSHELL_BIN` | discovered from `PATH` and standard `pollen` locations |
| `REACHY_OPENSHELL_SANDBOX` | `reachy-agent` |
| `REACHY_OPENSHELL_AUDIO_SERVICE` | `audio` |
| `REACHY_OPENSHELL_AUDIO_PORT` | `8765` |
| `REACHY_OPENSHELL_GATEWAY_PORT` | `17670` |
| `REACHY_OPENSHELL_COMMAND_TIMEOUT_SECONDS` | `150` |

The app process must run as a user that can read the OpenShell gateway
configuration. On the standard Reachy image this is expected to be `pollen`,
but verify it during device installation.

## Development checks

From the parent project:

```bash
PYTHONPATH=native-controller/src uv run pytest -q native-controller/tests
uv run ruff check native-controller/src native-controller/tests
```

Build the installable package without installing the sandbox dependencies:

```bash
uv build --project native-controller
```

For a local Wireless robot install, copy the resulting universal wheel to
Reachy and install it in the daemon's shared app environment. This is Pollen's
documented manual-deployment path:

```bash
/opt/uv/uv pip install --no-cache \
  --python /venvs/apps_venv/bin/python \
  /home/pollen/reachy_mini_openshell_controller-0.2.0-py3-none-any.whl
```

The daemon API intentionally rejects `source_kind: local`; its install endpoint
is for catalog/Hugging Face apps. The wheel's `reachy_mini_apps` entry point
makes it discoverable after the manual install. It appears as
`reachy_mini_openshell_controller` and can then be started/stopped from the
Reachy dashboard. The same lifecycle can be exercised directly with:

```bash
curl -X POST \
  http://127.0.0.1:8000/api/apps/start-app/reachy_mini_openshell_controller
curl -X POST http://127.0.0.1:8000/api/apps/stop-current-app
```
