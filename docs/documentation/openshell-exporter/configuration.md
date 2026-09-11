---
title: Configure sources and delivery
description: Choose local files or an existing gateway and configure credentials, destinations, and durable storage.
agent_markdown: true
---

# Configure sources and delivery

All deployment paths run the same image and accept standard Collector YAML.
Choose one of these ready-to-edit configurations:

| Configuration | Input | Output |
| --- | --- | --- |
| [config.yaml](config.yaml) | Local OCSF JSONL files | Normalized recovery records in `/output/events.json`. |
| [gateway.yaml](gateway.yaml) | WatchSandbox and policy snapshots from an existing gateway | HTTPS CloudEvents plus local recovery records. |

## Read existing files

With the starter configuration, place authorized OCSF JSONL files in `input/`.
Each file must end in `.jsonl`, with one JSON object per line. Replace the
processor's `gateway_id` and `workspace` with your source identity. Keep these
values, `source_instance`, and file paths stable across restarts.

Retain files, including rotated files, until collected. One exporter owns each
writable checkpoint directory. Do not also collect the same files through a
forwarder.

## Connect a gateway and destination

You need an existing HTTPS gateway, read-only gateway credentials, an authorized
sandbox name, and an HTTPS receiver that accepts CloudEvents JSON batches. This
setup does not create a gateway, agent, inference route, or destination.

For Docker or Podman, download the gateway profile as your active configuration:

```sh
curl -fL https://nvidia.github.io/OpenShell-Research/documentation/openshell-exporter/gateway.yaml -o config.yaml
```

Add these non-secret settings to `.env`, replacing the examples. Keep the UID/GID
entries already created by the Docker quickstart:

```dotenv
OPENSHELL_ENDPOINT=https://gateway.example.com:8080
OPENSHELL_GATEWAY_ID=gateway-1
OPENSHELL_WORKSPACE=default
OPENSHELL_SANDBOX_NAME=my-sandbox
OPENSHELL_EXPORT_URL=https://receiver.example.com/v1/events
```

Place the raw gateway token in `secrets/gateway-token` and the separate raw
destination token in `secrets/destination-token`, without the `Bearer` prefix.
Make the files readable only by the user running the exporter, for example with
`chmod 600 secrets/gateway-token secrets/destination-token`. Keep credentials
out of `.env` and version control.

For private CAs, place certificates in `secrets/` and add `tls.ca_file` under the
appropriate receiver/exporter, using paths such as `/run/secrets/gateway-ca.pem`.
For mTLS, add paired `tls.cert_file` and `tls.key_file` paths. Retain verified TLS;
do not disable verification to work around a missing CA.

Validate and recreate the container using the [Docker](docker.md) or
[Podman](podman.md) instructions. For Kubernetes, the [Helm guide](helm.md) mounts
these same files from a Secret and supplies settings through values.

Generate authorized activity in the selected sandbox and confirm records arrive
at your destination. Check the recovery file too when running locally. A healthy
process does not establish end-to-end delivery.

## Operate and extend

Keep `/state/checkpoints`, `/state/cloudevents-queue`, and `/output` persistent.
Recovery output appends across restarts. Queue capacity is bounded; monitor disk
space, restrict access to retained evidence, and arrange retention according to
your environment. Never delete state automatically to recover space.

CloudEvents receivers must deduplicate by `(source,id)` because delivery can be
retried. WatchSandbox cannot resume a disconnected stream; policy snapshots do
not reconstruct every missed change. Malformed input is marked rather than
silently dropped.

Inspect additional components with `docker run --rm IMAGE components` (or
`podman run --rm IMAGE components`), replacing `IMAGE` with your pinned exporter
image. Relay and native OTLP need separate pipelines, authenticated TLS inputs,
and their own destination queues. Use the `relay` processor with
`privacy.mode: allow` for Relay input before delivery.

The optional `ghcr.io/nvidia/openshell-otlp-proxy:v0.0.5-rc.1` image adds a
workload-local OTLP/HTTP queue. It does not filter content; its queue may retain
unfiltered input. The basic deployments above do not need this proxy. See the
[release status](index.md) before using either image.
