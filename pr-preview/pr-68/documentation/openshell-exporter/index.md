---
title: Export OpenShell evidence
description: Collect OpenShell evidence with a prebuilt image. Start locally, then deploy with Docker, Podman, or Helm.
agent_markdown: true
---

# Export OpenShell evidence

OpenShell Event Exporter collects security records, sandbox activity, policy
snapshots, and agent traces. It normalizes and redacts evidence, then sends it to
your security or observability tools as CloudEvents or OTLP.

Use a prebuilt image from GHCR. You do not need Go, a source checkout, or an
image build. This is an experimental research tool, provided as-is. Use at your
own risk.

Each release image targets Linux AMD64 and ARM64. Docker Desktop on Apple Silicon
Macs runs the Linux ARM64 image in its VM; Intel Macs use Linux AMD64. The same
image reference selects the matching architecture automatically.

> **Release pending:** the `v0.0.5-rc.1` image references in these guides are
> awaiting public publication and anonymous-pull verification.

## Choose your deployment

| Deployment | Start here |
| --- | --- |
| Docker / Docker Desktop | Try the quickstart below, then [manage the Compose deployment](docker.md). |
| Rootless Podman on Linux | [Run the same image with Podman](podman.md). |
| Kubernetes with Helm | [Install with the OpenTelemetry Helm chart](helm.md). |

## Quickstart with Docker

You need Docker with Compose v2 (Linux AMD64/ARM64, or Docker Desktop on Intel or
Apple Silicon macOS), `curl`, and a Bash-compatible shell. Keep local
port `23133` free. This first run reads a local JSONL file; it needs no gateway or
inference credentials.

Download the configuration and Compose file into an empty working directory:

```sh
mkdir openshell-exporter && cd openshell-exporter
mkdir input state output secrets
chmod 700 state output secrets
export DOCS_URL=https://nvidia.github.io/OpenShell-Research/documentation/openshell-exporter
curl -fL "$DOCS_URL/config.yaml" -o config.yaml
curl -fL "$DOCS_URL/compose.yaml" -o compose.yaml
printf 'EXPORTER_UID=%s\nEXPORTER_GID=%s\n' "$(id -u)" "$(id -g)" > .env
docker compose run --rm exporter validate --config /etc/exporter.yaml
docker compose up -d --wait
```

Append a synthetic record and inspect the normalized output:

```sh
printf '%s\n' '{"class_uid":4001,"activity_id":1,"time":1789084800000,"severity_id":1,"message":"synthetic quickstart event"}' >> input/sample.jsonl
sleep 2
cat output/events.json
curl --fail http://127.0.0.1:23133/
```

`output/events.json` should contain the sample message inside a normalized event.
The sample tests collection; it is not real gateway evidence. Incomplete records
are marked rather than dropped. Health alone does not prove delivery.

Stop with `docker compose stop`; resume with `docker compose start`. Your input,
checkpoints, and output stay in this directory. Do not delete `state/` to fix a
startup problem.

## Connect your environment

[Configure sources and delivery](configuration.md) to collect existing OCSF files
or connect a gateway to an HTTPS CloudEvents destination. The deployment guides
use the same Collector configuration and persistent storage layout.

The image also includes Relay privacy processing, native OTLP, file forwarding,
and edge/central components. Enable only the components you need. The optional
OTLP proxy has its own image; it is not required for the quickstart.

WatchSandbox cannot resume missed stream history. Policy reads are snapshots,
and redaction is not DLP. Treat exported evidence as sensitive and deduplicate
CloudEvents by `(source,id)`.

[Report an issue](https://github.com/NVIDIA/OpenShell-Research/issues) with the
image version and sanitized configuration. Licenses and notices are included
under `/usr/share/licenses/` in the images.
