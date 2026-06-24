# Reachy Mini OpenShell Research

Minimal research scaffold for running a Reachy Mini backend inside an OpenShell sandbox while starting against the Reachy Mini MuJoCo simulator.

## Current Shape

- Host process: runs `reachy-mini-daemon --sim`, which starts the simulator and exposes the daemon API on port `8000`.
- OpenShell process: runs this backend in a sandbox and connects to the host daemon through `host.openshell.internal:8000`.
- First backend surface: `GET /health` for daemon reachability and `POST /moves/smoke` for a small head/antenna motion.
- Default SDK media mode: `no_media`, so the motion-only smoke path does not require camera, audio, or WebRTC access.

## Local Simulator Quickstart

Prerequisites:

- `uv`
- Python 3.10-3.12. Python 3.12 is recommended by the Reachy Mini docs and used below.
- On Linux, install the Reachy Mini GStreamer prerequisites before installing the SDK. The upstream guide lists the full package set and notes that Ubuntu 22.04 needs newer GStreamer packages.

```bash
cd projects/reachy-mini-openshell
uv venv --python 3.12
source .venv/bin/activate
uv pip install -e ".[backend,sim]"
```

Start the simulator:

```bash
reachy-mini-daemon --sim --scene minimal
```

In another terminal:

```bash
source .venv/bin/activate
python -m reachy_openshell.smoke
python -m reachy_openshell.backend
```

Then probe the backend:

```bash
curl http://127.0.0.1:8080/health
curl -X POST http://127.0.0.1:8080/moves/smoke
```

On macOS, the Reachy docs recommend launching the simulator with `mjpython` instead:

```bash
mjpython -m reachy_mini.daemon.app.main --sim
```

## OpenShell Sandbox Path

For the sandboxed backend to reach a host-level simulator, bind the Reachy daemon to an interface visible from containers:

```bash
reachy-mini-daemon --sim --scene minimal --fastapi-host 0.0.0.0 --fastapi-port 8000
```

If your installed daemon exposes different flag names, check `reachy-mini-daemon --help`; the important part is that the daemon is reachable from the sandbox at `host.openshell.internal:8000`.

Build and run the backend in OpenShell:

```bash
openshell status
openshell sandbox create \
  --name reachy-mini-backend \
  --from . \
  --policy openshell/policy.local-sim.yaml \
  --env REACHY_HOST=host.openshell.internal \
  --env REACHY_PORT=8000 \
  --env REACHY_CONNECTION_MODE=network \
  --env REACHY_MEDIA_BACKEND=no_media \
  -- python3 -m reachy_openshell.backend --host 0.0.0.0 --port 8080
```

Expose the backend service:

```bash
openshell service expose reachy-mini-backend 8080
```

The sandbox image installs the Linux system packages needed by the Reachy SDK before installing the Python package.

## Layout

- `src/reachy_openshell/smoke.py`: SDK smoke motion for the simulator or a real robot daemon.
- `src/reachy_openshell/backend.py`: small FastAPI backend for health checks and the smoke motion.
- `openshell/policy.local-sim.yaml`: starter OpenShell policy allowing Python to reach the host simulator daemon.
- `Dockerfile`: OpenShell sandbox image for the backend, based on the OpenShell community base sandbox.

## References

- Reachy Mini simulator setup: https://huggingface.co/docs/reachy_mini/en/platforms/simulation/get_started
- Reachy Mini Linux GStreamer prerequisites: https://huggingface.co/docs/reachy_mini/en/SDK/gstreamer-installation
- Reachy Mini SDK API: https://huggingface.co/docs/reachy_mini/en/API/reachymini
- OpenShell overview: https://docs.nvidia.com/openshell/about/overview
- OpenShell sandbox management: https://docs.nvidia.com/openshell/sandboxes/manage-sandboxes
- OpenShell policy reference: https://docs.nvidia.com/openshell/reference/policy-schema
