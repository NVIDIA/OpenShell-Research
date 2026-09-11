---
title: "Compatibility"
description: "OpenShell Event Exporter — compatibility."
agent_markdown: true
---

# Compatibility

Current experimental research example:
[v0.0.4](https://github.com/NVIDIA/OpenShell-Research/releases).
Pins are compatibility targets, not a production-readiness claim.

| Surface | Target |
|---|---|
| OpenShell gateway | `v0.0.113` |
| OpenShell Go SDK | `v0.0.0-20260825132042-455883905a7a` |
| OCSF | `1.8.0`; `1.7.0` accepted for replay |
| Collector contrib/core | `v0.160.0` / `v1.66.0` |
| Go | `1.26.8` |
| NeMo Relay | `v0.7.1` |
| Platforms | Linux AMD64 and ARM64; macOS uses a Linux container runtime |

## Limitations

WatchSandbox has no resume cursor; reconnect tails cannot guarantee replay.
There is no public resumable WatchEvents RPC in the pinned SDK. Policy APIs
provide current snapshots, not every intermediate change. OCSF validation is
structural, not full class-specific conformance. Unknown fields are preserved
in the redacted original; consumers must tolerate additive envelope fields.

Real source coverage, natural file rotation/restart/outage reconciliation,
Kubernetes node loss/scaling/credential rotation, external CloudEvents and OTLP
destinations, capacity, and independent privacy/security review remain incomplete.
Use the [qualification harnesses](https://github.com/NVIDIA/OpenShell-Research/tree/main/projects/openshell-exporter/integration/qualification) and
[destination conformance tests](https://github.com/NVIDIA/OpenShell-Research/blob/main/projects/openshell-exporter/conformance/README.md) for your environment.
Local tests do not establish external compatibility or complete coverage.

Build images from the same tagged source version for the exporter and proxy.
No project-published images are required. See [build instructions](https://github.com/NVIDIA/OpenShell-Research/blob/main/projects/openshell-exporter/deploy/README.md)
and [release notes](https://github.com/NVIDIA/OpenShell-Research/blob/main/projects/openshell-exporter/release/notes/v0.0.4.md).
