---
title: "OpenShell Event Exporter"
description: "OpenShell Event Exporter — openshell event exporter."
agent_markdown: true
---

# OpenShell Event Exporter

Send OpenShell security events, sandbox activity, policy snapshots, and agent
traces to your security and observability tools. The exporter preserves source
context, redacts known secrets, and delivers CloudEvents over HTTPS or native OTLP.

Experimental research example, provided as-is. Use at your own risk.

## Quick start: see real evidence locally

The Docker demo starts its own OpenShell gateway, runs a Hermes agent task with
Nemotron, and exports the resulting events and Relay traces to a local viewer.

You need **Linux x86_64 or macOS with Docker Desktop**, Docker Compose, Bash,
Git, `curl`, `jq`, `openssl`, and an NVIDIA API key with access to Nemotron 3 Super.
Apple Silicon also needs Rosetta support for the demo's AMD64 sandbox.
Keep ports 8080, 8081, 8088, 8888, and 13133 free. The first run downloads and builds
large images; hosted inference may incur usage charges.

If monitoring ports are occupied, set `export DEMO_METRICS_PORT=18888` and
`export DEMO_HEALTH_PORT=23133` before starting. The demo saves these settings
for verification and shutdown.

**1. Start.** From `projects/openshell-exporter/` in your Research checkout:

```sh
cd examples/demo/real-gateway
bash -c '
  read -r -s -p "NVIDIA API key: " NVIDIA_API_KEY && printf "\n" || exit 1
  export NVIDIA_API_KEY
  exec ./run.sh
'
```

Paste your key at the hidden prompt. The script checks prerequisites, prepares
local credentials, starts the stack, runs the task, and verifies delivery.

**2. Inspect.** After the script succeeds, open
[the local evidence viewer](https://127.0.0.1:8088). Trust `runtime/tls/ca.crt`
only for this demo. You should see sandbox events, a denied network request,
and privacy-filtered agent traces. The viewer is a development example.
Verification reports are in `runtime/evidence/`; redacted recovery is in
`runtime/recovery/`.

**3. Run another task or stop.** In the same directory:

```sh
./run-live-task.sh
./verify.sh
./down.sh
```

`down.sh` stops the demo and removes its sandbox while retaining evidence and
state. For SSH access, custom prompts, and troubleshooting, see the
[demo guide](https://github.com/NVIDIA/OpenShell-Research/blob/main/projects/openshell-exporter/examples/demo/real-gateway/README.md).

## Connect your environment

| What you want | Where to start |
|---|---|
| Build an image or push to your own registry | [Image builds](https://github.com/NVIDIA/OpenShell-Research/blob/main/projects/openshell-exporter/deploy/README.md) |
| Collect from your existing gateway | [Docker setup](https://github.com/NVIDIA/OpenShell-Research/blob/main/projects/openshell-exporter/deploy/docker/README.md) |
| Export OCSF or operational files | [Configuration examples](configuration.md) |
| Add Relay or native gateway traces | [Trace configuration](configuration.md#traces-and-privacy) |
| Investigate in Elastic/Kibana | [Local Elastic demo](https://github.com/NVIDIA/OpenShell-Research/blob/main/projects/openshell-exporter/examples/demo/real-gateway/elastic/README.md) or [integration pack](https://github.com/NVIDIA/OpenShell-Research/blob/main/projects/openshell-exporter/integrations/elastic/README.md) |
| Run on Kubernetes with sandbox forwarding | [Helm setup](https://github.com/NVIDIA/OpenShell-Research/blob/main/projects/openshell-exporter/deploy/kubernetes/README.md) |
| Connect your own Hermes agent | [Hermes integration](https://github.com/NVIDIA/OpenShell-Research/blob/main/projects/openshell-exporter/integrations/hermes/README.md) |
| Run without Docker | [Rootless Podman](https://github.com/NVIDIA/OpenShell-Research/blob/main/projects/openshell-exporter/deploy/podman/README.md) or [source build](configuration.md#run-a-file-source) |
| Separate collection and delivery | [Edge/central profiles](configuration.md#deployment-profiles) |
| Connect another receiver or OTLP backend | [Event contract](event-model.md) |

The exporter supports stable event identity, recursive redaction, source-gap
reporting, correlation, persistent delivery queues, recovery output, and health
metrics. Enable only the sources you need.

WatchSandbox is non-resumable; policy snapshots are not a complete history.
File replay needs retained source files and persistent state. Receivers must
deduplicate by `(source,id)`. Redacted data remains sensitive, and time-based
correlation is not causal proof. The exporter never makes decisions or changes policy.

[Operations](operations.md) · [Compatibility](compatibility.md) ·
[Release v0.0.4](https://github.com/NVIDIA/OpenShell-Research/releases) ·
[Contributing](https://github.com/NVIDIA/OpenShell-Research/blob/main/projects/openshell-exporter/CONTRIBUTING.md) · [Releasing](release.md) · [Security](https://github.com/NVIDIA/OpenShell-Research/blob/main/projects/openshell-exporter/SECURITY.md)

Apache-2.0; retain [LICENSE](https://github.com/NVIDIA/OpenShell-Research/blob/main/projects/openshell-exporter/LICENSE) and [NOTICE](https://github.com/NVIDIA/OpenShell-Research/blob/main/projects/openshell-exporter/NOTICE). Contributions require DCO sign-off.
