# Reachy Mini + OpenShell

This project demonstrates how OpenShell can constrain an AI-controlled physical
Reachy Mini by mediating direct REST calls from a sandboxed conversation app.

## Start here

Follow the **[Reachy Mini OpenShell sandbox tutorial](../reachy-mini-openshell-sandbox.md)**
for the complete, copy-and-run build.

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
| OpenShell gateway | Sandbox host | Creates the sandbox and enforces REST method/path policy. |
| Conversation application | OpenShell sandbox | Runs the Gradio UI and Realtime conversation session. |

## Project resources

- [Full step-by-step tutorial](../reachy-mini-openshell-sandbox.md)
- [Application source and README](https://github.com/NVIDIA/OpenShell-Research/tree/main/projects/reachy-mini-openshell)
- [Motion-disabled policy](https://github.com/NVIDIA/OpenShell-Research/blob/main/projects/reachy-mini-openshell/openshell/policy-motion-disabled.yaml)
- [Head-motion-enabled policy](https://github.com/NVIDIA/OpenShell-Research/blob/main/projects/reachy-mini-openshell/openshell/policy-head-motion-enabled.yaml)
- [Sandbox Dockerfile](https://github.com/NVIDIA/OpenShell-Research/blob/main/projects/reachy-mini-openshell/Dockerfile.openshell)

## Two ways to use the project

### Run the application locally

Use the application README for simulator-first development or direct operation
without an OpenShell sandbox.

### Build the OpenShell policy POC

Use the sandbox tutorial when you want to demonstrate direct REST restrictions,
visible policy denials, and the physical Reachy Mini architecture.
