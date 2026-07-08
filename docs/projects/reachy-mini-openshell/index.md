# Reachy Mini + OpenShell

This project demonstrates how OpenShell can constrain an AI-controlled physical
Reachy Mini while still supporting conversation, camera analysis, scene scans,
and expressive movement.

## Start here

Follow the **[Reachy Mini OpenShell sandbox tutorial](../reachy-mini-openshell-sandbox.md)**
for the complete, copy-and-run build.

The tutorial starts with the robot runtime on your laptop and progressively
adds:

1. An authenticated Reachy MCP server with a deliberately small hardware API.
2. Local and MCP tool transports in the conversation application.
3. An OpenShell sandbox around the AI-facing application.
4. Separate OpenAI Realtime and approved vision-model routes.
5. OpenShell policy rules that permit camera workflows while denying selected
   physical tools.
6. Browser and command-line tests that make policy decisions visible.

## What runs where

| Component | Location | Responsibility |
| --- | --- | --- |
| Reachy daemon | Reachy Mini | Controls the physical motors, camera, and media devices. |
| Reachy MCP server | Laptop host | Exposes the approved robot capability surface. |
| OpenShell gateway | Laptop host | Creates the sandbox and enforces policy. |
| Conversation application | OpenShell sandbox | Runs the Gradio UI and Realtime conversation session. |
| Vision route | OpenShell managed inference | Sends images only to the configured approved model. |

## Project resources

- [Full step-by-step tutorial](../reachy-mini-openshell-sandbox.md)
- [Application source and README](https://github.com/NVIDIA/OpenShell-Research/tree/main/projects/reachy-mini-openshell)
- [Safe OpenShell policy](https://github.com/NVIDIA/OpenShell-Research/blob/main/projects/reachy-mini-openshell/openshell/policy-safe.yaml)
- [Sandbox Dockerfile](https://github.com/NVIDIA/OpenShell-Research/blob/main/projects/reachy-mini-openshell/Dockerfile.openshell)

## Two ways to use the project

### Run the application locally

Use the application README for simulator-first development or direct operation
without an OpenShell sandbox.

### Build the OpenShell policy POC

Use the sandbox tutorial when you want to demonstrate model routing, MCP tool
restrictions, visible policy denials, and the physical Reachy Mini architecture.
