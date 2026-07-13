# Reachy Mini + OpenShell

This project is a reference implementation for running OpenShell on an edge
device. A conversation agent runs in an onboard sandbox, trusted native code
owns the hardware, and local policy mediates the REST calls that can produce
physical effects on Reachy Mini.

## Start here

Read **[Designing OpenShell for the Edge: Why Policy Has to Live Next to the
Action](../../dev-notes/posts/2026-07-13-policy-controlling-reachy-mini-with-openshell.md)**
for the reusable edge architecture, implementation decisions, challenges, and
demo result.

Follow the **[project-local onboard setup
tutorial](https://github.com/NVIDIA/OpenShell-Research/blob/kirit93/reachy-implementation/projects/reachy-mini-openshell/ONBOARD_SETUP.md)**
for the copy-and-run build, installation, policy verification, voice demo, and
troubleshooting steps.

The tutorial progressively adds:

1. A fixed REST tool transport for `move_head` and `stop_motion`.
2. An OpenShell sandbox around the AI-facing application.
3. Direct calls to the Reachy daemon on port `8000`.
4. REST policy rules that enable or disable `/api/move/goto`.
5. Browser and command-line tests that make policy decisions visible.

## What runs where

| Component | Location | Responsibility |
| --- | --- | --- |
| Reachy daemon | Reachy Mini | Controls the physical motors and exposes the REST API. |
| OpenShell gateway | Reachy Mini | Creates the onboard sandbox and enforces REST method/path policy. |
| Conversation application | OpenShell sandbox on Reachy | Runs the Realtime conversation session and fixed REST tools. |
| Native controller | Reachy App on Reachy | Owns microphone, speaker, and camera; starts/stops the sandbox agent. |

## Project resources

- [Implementation Dev Note](../../dev-notes/posts/2026-07-13-policy-controlling-reachy-mini-with-openshell.md)
- [Onboard setup and troubleshooting tutorial](https://github.com/NVIDIA/OpenShell-Research/blob/kirit93/reachy-implementation/projects/reachy-mini-openshell/ONBOARD_SETUP.md)
- [Application source and README](https://github.com/NVIDIA/OpenShell-Research/tree/kirit93/reachy-implementation/projects/reachy-mini-openshell)
- [Motion-disabled policy](https://github.com/NVIDIA/OpenShell-Research/blob/kirit93/reachy-implementation/projects/reachy-mini-openshell/openshell/policy-motion-disabled.yaml)
- [Camera-enabled, motion-disabled policy](https://github.com/NVIDIA/OpenShell-Research/blob/kirit93/reachy-implementation/projects/reachy-mini-openshell/openshell/policy-camera-enabled-motion-disabled.yaml)
- [Head-motion-enabled policy](https://github.com/NVIDIA/OpenShell-Research/blob/kirit93/reachy-implementation/projects/reachy-mini-openshell/openshell/policy-head-motion-enabled.yaml)
- [Sandbox Dockerfile](https://github.com/NVIDIA/OpenShell-Research/blob/kirit93/reachy-implementation/projects/reachy-mini-openshell/Dockerfile.openshell)

## Two ways to use the project

### Run the application locally

Use the application README for simulator-first development or direct operation
without an OpenShell sandbox.

### Build the OpenShell policy POC

Use the onboard setup tutorial when you want to demonstrate direct REST
restrictions, visible policy denials, and the physical Reachy Mini architecture.
