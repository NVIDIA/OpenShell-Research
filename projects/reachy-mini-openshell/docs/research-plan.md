# Research Plan

## Working Hypothesis

Keep the simulator on the host for the first spike so MuJoCo can open its viewer normally. Run only the backend in OpenShell. The sandbox reaches the host daemon through `host.openshell.internal:8000`, using a narrow OpenShell network policy.

## Phase 0: Simulator Bootstrap

- Install `reachy-mini[mujoco]` locally.
- Start `reachy-mini-daemon --sim --scene minimal`.
- Verify the Python SDK can connect and run the smoke motion.

## Phase 1: Sandboxed Backend

- Build the local OpenShell sandbox image from `Dockerfile`.
- Run `python3 -m reachy_openshell.backend` inside the sandbox.
- Confirm `GET /health` reaches the simulator daemon.
- Confirm `POST /moves/smoke` moves the simulated robot.

## Phase 2: Policy Tightening

- Capture the exact daemon traffic used by the SDK.
- Replace the current L4 passthrough policy with REST and WebSocket rules if the daemon API remains stable enough.
- Decide whether PyPI access is needed at runtime or only at image build time.

## Open Questions

- Should the simulator stay host-side for development, or should a headless simulator move into its own OpenShell sandbox later?
- Which daemon bind flags are stable across Reachy Mini SDK versions and operating systems?
- Do audio and camera paths need to be disabled or mocked for OpenShell-based backend tests?
