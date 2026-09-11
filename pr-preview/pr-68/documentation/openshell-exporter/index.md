---
title: OpenShell Event Exporter
description: Install the container image, collect OpenShell evidence, and configure delivery.
agent_markdown: true
---

# OpenShell Event Exporter

Collect OpenShell security records, sandbox activity, policy snapshots, and agent
traces. The exporter normalizes and redacts evidence, preserves stable event
identity, and delivers CloudEvents over HTTPS or native OTLP.

Experimental research tool, provided as-is. Use at your own risk.

> Release candidate: the image names below are reserved for `v0.0.5-rc.1`.
> Public image publication and anonymous-pull verification are pending.

## Install and try it

You need Docker on Linux, or Docker Desktop, and `curl`. Images are distributed
through GitHub Container Registry (GHCR); no source checkout or Go installation
is required. Run these commands in a Bash-compatible shell:

```sh
mkdir openshell-exporter
cd openshell-exporter
mkdir input state output
chmod 700 state output
curl --fail --location --output config.yaml \
  https://nvidia.github.io/OpenShell-Research/documentation/openshell-exporter/config.yaml
export EXPORTER_IMAGE=ghcr.io/nvidia/openshell-exporter:v0.0.5-rc.1
docker pull "$EXPORTER_IMAGE"
docker run --rm "$EXPORTER_IMAGE" --version
docker run --rm --user "$(id -u):$(id -g)" \
  --mount "type=bind,src=$PWD/config.yaml,dst=/etc/exporter.yaml,readonly" \
  "$EXPORTER_IMAGE" validate --config /etc/exporter.yaml
```

The [starter configuration](config.yaml) reads OCSF JSONL files from `input/` and
writes normalized, redacted Collector JSON to `output/events.json`. It needs no
gateway or inference credentials. Start it:

```sh
docker run -d --name openshell-exporter \
  --user "$(id -u):$(id -g)" --read-only --tmpfs /tmp \
  --cap-drop ALL --security-opt no-new-privileges --memory 512m \
  -p 127.0.0.1:23133:13133 \
  --mount "type=bind,src=$PWD/config.yaml,dst=/etc/exporter.yaml,readonly" \
  --mount "type=bind,src=$PWD/input,dst=/input,readonly" \
  --mount "type=bind,src=$PWD/state,dst=/state" \
  --mount "type=bind,src=$PWD/output,dst=/output" \
  "$EXPORTER_IMAGE" --config /etc/exporter.yaml
curl --fail --retry 10 --retry-all-errors --retry-delay 1 http://127.0.0.1:23133/
```

Copy authorized OpenShell OCSF files into `input/` with a `.jsonl` suffix. To test
without real records, append this explicitly synthetic event:

```sh
printf '%s\n' '{"class_uid":4001,"activity_id":1,"time":1789084800000,"severity_id":1,"message":"synthetic quickstart event"}' >> input/sample.jsonl
sleep 2
cat output/events.json
docker logs --tail 20 openshell-exporter
```

The sample demonstrates collection; it is not gateway evidence. Malformed or
incomplete records are marked, not silently dropped. Health indicates the process
is running; confirm records arrive at the intended destination to verify delivery.

Stop with `docker stop openshell-exporter`. Resume with `docker start
openshell-exporter`. To change configuration, stop and remove the container with
`docker rm openshell-exporter`, then rerun the start command. Keep `input/`,
`state/`, and `output/`: deleting them can lose evidence or replay checkpoints.

## Configure sources

Edit `config.yaml`, validate it, then recreate the container. Keep `gateway_id`,
`workspace`, and `source_instance` stable and unique to the source. The
`source_profiles` list describes enabled inputs; it does not create receivers.

For an existing gateway, add this entry under `receivers` and replace the endpoint,
workspace, gateway ID, and sandbox names with your authorized scope:

```yaml
  watchsandbox:
    endpoint: https://gateway.example.com:8080
    gateway_id: gateway-1
    workspace: default
    sandbox_names: [my-sandbox]
    token_env: ""
    token_file: /run/secrets/gateway-token
    tls:
      ca_file: /run/secrets/gateway-ca.pem
    policy_reconciliation:
      enabled: true
      storage: file_storage/checkpoints
      include_effective_policies: false
```

Use read-only gateway credentials. Add read-only bind mounts for the token and
CA files to both validation and startup commands. For mTLS, also mount the client
certificate and key and set `tls.cert_file` and `tls.key_file`. Configure the
processor with the same gateway/workspace and
`source_profiles: [ocsf.file, watchsandbox, policy.reconciliation]`; change the
logs pipeline receivers to `[file_log/ocsf, watchsandbox]`. Remove the file receiver
and its profile if you only want gateway streams.

WatchSandbox cannot resume a disconnected stream. Policy reconciliation reads
snapshots; it does not reconstruct all missed history. Retain OCSF files until
read, preserve their paths across restarts, and collect each file through one
writer only.

## Send CloudEvents over HTTPS

Add these entries under `extensions`:

```yaml
  bearertokenauth/destination:
    filename: /run/secrets/destination-token
  file_storage/cloudevents_queue:
    directory: /state/cloudevents-queue
    create_directory: true
```

Add a destination under `exporters`, using your receiver's HTTPS URL:

```yaml
  cloudevents:
    endpoint: https://receiver.example.com/v1/events
    auth:
      authenticator: bearertokenauth/destination
    retry_on_failure:
      enabled: true
      max_elapsed_time: 0s
    sending_queue:
      enabled: true
      storage: file_storage/cloudevents_queue
      queue_size: 10000
      num_consumers: 4
      block_on_overflow: true
```

Mount the destination token file read-only. For a private CA, add `tls.ca_file`
and mount that CA too. Add `bearertokenauth/destination` and
`file_storage/cloudevents_queue` to `service.extensions`, then set the logs
pipeline exporters to `[cloudevents, file/recovery]`. Add
`cloudevents_queue: /state/cloudevents-queue` to `storagehealth.paths`.

The endpoint must accept CloudEvents HTTP JSON batches and deduplicate by
`(source,id)`. Delivery can be retried; it is not exactly once. Preserve separate
checkpoint, queue, and recovery paths. Do not share writable state between
exporters. Restrict access to recovery files and monitor disk capacity.

## Other components and deployments

The exporter image also includes Relay privacy processing, native OTLP receivers
and exporters, file forwarding, and edge/central evidence processing. Inspect its
installed components with:

```sh
docker run --rm "$EXPORTER_IMAGE" components
```

Keep Relay and native gateway OTLP in separate pipelines. Relay traces require
`relay` processing with `privacy.mode: allow` before delivery. Use authenticated
TLS inputs, a separate destination credential, and persistent queues. Redaction
is not DLP; exported evidence still needs access controls.

An optional workload-local trace proxy is distributed as
`ghcr.io/nvidia/openshell-otlp-proxy:v0.0.5-rc.1`. It forwards OTLP/HTTP with a
persistent queue and does not apply the exporter's privacy filtering. Deploy it
only when that extra queue is needed; its storage can contain unfiltered data.

Docker, Podman, and Kubernetes can pull the same images. For Kubernetes, use the
image in your Deployment, mount Collector configuration through a ConfigMap,
credentials through Secrets, and state/output through persistent volumes. Run
non-root, disable service-account token mounting, and keep health and telemetry
ports private. Use one replica per writable state volume. No image build inside
the cluster is required.

Pin an image version or digest, retain state, and validate configuration before
upgrading. The images include the project's Apache-2.0 license and notices at
`/usr/share/licenses/openshell-exporter/`, with Go and third-party licenses
alongside them under `/usr/share/licenses/`. Report issues through
[OpenShell Research](https://github.com/NVIDIA/OpenShell-Research/issues), including
the image digest and sanitized configuration; never include tokens or raw evidence.
