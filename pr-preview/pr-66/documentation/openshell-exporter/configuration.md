---
title: "Configuration"
description: "OpenShell Event Exporter — configuration."
agent_markdown: true
---

# Configuration

For the first local run, use the [quick start](index.md#quick-start-see-real-evidence-locally).
To connect your own sources, use Collector YAML and enable only needed sources. Copy an example and replace
its identity, source paths, endpoints, credentials, and storage locations.

| Input | Example | Limit |
|---|---|---|
| OCSF JSONL | [config.ocsf.yaml](https://github.com/NVIDIA/OpenShell-Research/blob/main/projects/openshell-exporter/examples/config.ocsf.yaml) | Retain source files and checkpoints |
| OpenShell operational files | [config.openshell-log.yaml](https://github.com/NVIDIA/OpenShell-Research/blob/main/projects/openshell-exporter/examples/config.openshell-log.yaml) | Allow-listed OpenShell files only |
| WatchSandbox and policy reads | [Docker config](https://github.com/NVIDIA/OpenShell-Research/blob/main/projects/openshell-exporter/deploy/docker/config.yaml) | Live stream plus periodic snapshots; no event cursor |
| Combined sources | [config.full.yaml](https://github.com/NVIDIA/OpenShell-Research/blob/main/projects/openshell-exporter/examples/config.full.yaml) | Configure every enabled input and destination |
| Relay traces | [config.nemo-relay.yaml](https://github.com/NVIDIA/OpenShell-Research/blob/main/projects/openshell-exporter/examples/config.nemo-relay.yaml) | Opt-in privacy filtering |
| Relay files | [config.nemo-relay-file.yaml](https://github.com/NVIDIA/OpenShell-Research/blob/main/projects/openshell-exporter/examples/config.nemo-relay-file.yaml) | Explicit file profile |
| Kubernetes context and forwarding | [Helm](https://github.com/NVIDIA/OpenShell-Research/blob/main/projects/openshell-exporter/deploy/kubernetes/README.md) | Namespace-scoped context; one forwarder per source |

## Identity and files

Keep `gateway_id`, `workspace`, and `source_instance` stable. `source_profiles`
declares expected capability; it does not create receivers. Keep validation mode
`mark` so malformed evidence remains visible.

Mount source files read-only under stable per-sandbox paths. Preserve path,
resolved path, record number, and byte offset in file receivers. Retain rotated
files until read; do not collect the same file directly and through a forwarder.
The examples split lines above 8 MiB and retain error provenance.

## Gateway and policy reads

WatchSandbox requires the endpoint, workspace/selector, gateway ID, and read-only
authentication. Use `token_file` or `token_env`, not both. Configure trusted CA
and paired client certificate/key when using mTLS. Source auth supports bearer,
mTLS, or both; insecure HTTP is for explicit local fixtures only.

Policy reconciliation requires persistent checkpoint storage. `max_revisions`
bounds snapshots; partial reads emit warnings and do not advance checkpoints.
Review tokens are removed; effective policy bodies are disabled by default.

## Traces and privacy

Keep Relay and native gateway OTLP on separate receivers and privacy profiles.
Every input needs TLS and input credentials; every OTLP destination needs its own
credential and queue. Trace intake requires an OTLP destination.

The Relay allow profile removes prompt/response content, tool arguments/results,
status messages, credentials, and unknown attributes. Review any customization.
The [optional proxy](https://github.com/NVIDIA/OpenShell-Research/blob/main/projects/openshell-exporter/otlpproxy/README.md) queues unfiltered data before the
exporter's privacy processor; protect that storage accordingly.

## Storage and delivery

Keep separate persistent directories for checkpoints, CloudEvents queues, each
OTLP queue, and recovery output. Never share writable state across processes or
delete it to recover space. Queue sizes count requests, not outage minutes.

Use HTTPS and dedicated destination credentials. Prefer mounted secret files.
CloudEvents receivers must implement the [event contract](event-model.md).
Keep health (`13133`) and metrics (`8888`) private. See [operations](operations.md).

## Deployment profiles

Docker/Podman offer `core` (files and gateway), `full` (adds Relay), and `complete`
(adds native OTLP and forwarded files). Helm offers `stream`, `complete`, and
`custom`. Start with `standalone`; `edge` acquires and sanitizes locally while
`central` verifies the internal contract and delivers downstream. Central
replicas need private queues; drain before changing shard membership.

Validate before starting:

```sh
./_build/openshell-event-exporter validate --config config.local.yaml
# Packaged alternative; TARGET is docker, podman, or kubernetes:
./deploy/deploy.sh TARGET validate
```

## Run a file source

Requires Linux (AMD64 or ARM64), mise, readable OpenShell OCSF files, and an HTTPS
CloudEvents receiver with a destination token. For a packaged installation, use
[Docker](https://github.com/NVIDIA/OpenShell-Research/blob/main/projects/openshell-exporter/deploy/docker/README.md). Run these commands from the project root.

```sh
mise trust
mise install
mise run build
cp examples/config.ocsf.yaml config.local.yaml
mkdir -p state/checkpoints state/cloudevents-queue output
```

Edit `config.local.yaml` to select your authorized OCSF files. The example reads
`/var/log/openshell-ocsf.*.log`. Set a stable gateway ID and your receiver settings:

```sh
export OPENSHELL_GATEWAY_ID='gateway-1'
export OPENSHELL_WORKSPACE='default'
export OPENSHELL_EXPORT_URL='https://receiver.example.com/v1/events'
export OPENSHELL_EXPORT_CA_FILE='/path/to/receiver-ca.pem'
export OPENSHELL_EXPORT_CLIENT_CERT_FILE=''
export OPENSHELL_EXPORT_CLIENT_KEY_FILE=''
read -r -s -p 'Destination token: ' OPENSHELL_EXPORT_TOKEN
export OPENSHELL_EXPORT_TOKEN
./_build/openshell-event-exporter validate --config config.local.yaml
./_build/openshell-event-exporter --config config.local.yaml
```

The token prompt uses Bash. Use mounted secret files in packaged deployments.
Set both client-certificate paths if the destination requires mTLS.

In another terminal, check `http://127.0.0.1:13133/` and
`http://127.0.0.1:8888/metrics`. Generate authorized source activity and confirm
new records reach both `output/openshell-events.json` and the receiver. Process
health alone does not prove delivery. See [operations](operations.md).
