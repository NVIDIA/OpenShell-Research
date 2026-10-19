# OpenShell Event Exporter

Collect OpenShell events, normalize and redact records, and deliver CloudEvents
or OTLP. This self-contained Go module runs as a local process or a Docker
container, with the full exporter component set. Experimental research software,
provided as-is; use at your own risk.

For other deployment methods and more advanced features, contact
[delgadof@nvidia.com](mailto:delgadof@nvidia.com).

Gateway API collection requires OpenShell >=0.1.0; the current target is v0.1.3.
The first run below uses a sample file and needs no gateway.

## Quickstart: local process

Use [Go 1.27.2](https://go.dev/doc/install) and a Bash-compatible shell. Get the
source and enter the project directory:

```sh
git clone https://github.com/NVIDIA/OpenShell-Research.git
cd OpenShell-Research/projects/tools/openshell-exporter
```

The build downloads public dependencies. The synthetic sample needs no
credentials or model service.

```sh
go build -o bin/openshell-event-exporter ./cmd/openshell-event-exporter
mkdir -p input state output
chmod 700 state output
printf '%s\n' '{"class_uid":4001,"category_uid":4,"activity_id":1,"type_uid":400101,"time":1789120800000,"metadata":{"uid":"quickstart-1","version":"1.8.0"},"message":"OpenShell exporter quickstart"}' > input/example.jsonl
./bin/openshell-event-exporter validate --config config.yaml
./bin/openshell-event-exporter --config config.yaml
```

In another terminal, run `curl --fail http://127.0.0.1:13133/` and
`cat output/events.json`. Output contains `quickstart-1` in a normalized record.
The file uses OpenTelemetry JSON, not bare OCSF JSONL or a CloudEvents batch.
Ctrl-C stops the process. Restarting resumes saved file checkpoints; keep `state`
and `output`. Stop the local process before trying the container with those paths.

## Quickstart: Docker

Use Docker Engine or Docker Desktop. Create the input and directories as above,
then build and run from this project directory:

```sh
docker build -t openshell-event-exporter:local .
docker run --rm --name openshell-exporter \
  --user "$(id -u):$(id -g)" --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true --tmpfs /tmp:rw,noexec,nosuid,size=32m \
  --workdir /work -e EXPORTER_HEALTH_ENDPOINT=0.0.0.0:13133 \
  -p 127.0.0.1:13133:13133 \
  -v "$PWD/config.yaml:/work/config.yaml:ro" \
  -v "$PWD/input:/work/input:ro" \
  -v "$PWD/state:/work/state" -v "$PWD/output:/work/output" \
  openshell-event-exporter:local --config /work/config.yaml
```

The image health check probes `127.0.0.1:13133`; retain that port inside the
container. Its optional `healthcheck --writable DIRECTORY ...` command checks
persistence-directory permissions.

Use the same health and output checks as the local process. Saved checkpoints
prevent replay of the example; append a new record to test more delivery. Stop with
`docker stop openshell-exporter`. Build locally; no published image is required.
Linux AMD64/ARM64 and native macOS ARM64 are build targets; Docker on Apple Silicon
runs the Linux ARM64 image through Docker Desktop.

## Choose real inputs

For existing OCSF JSONL, set `receivers.file_log/ocsf.include` in [config.yaml](config.yaml)
to the readable source file glob. In Docker, mount the source directory read-only
and use its container path in the glob. Mount the directory, so rotated files remain
visible. Logs inside an OpenShell sandbox must first be made accessible to the
exporter; this sample does not install a sandbox agent.

[template.config.yaml](template.config.yaml) describes all exporter-specific
settings and included component types, including gateway collection, OTLP, privacy
filtering, and delivery. Select the needed pipelines and mount their credentials
and persistent storage. The template is a reference, not a ready-to-run deployment.
`openshell-event-exporter components` lists the full component set.

### Gateway and supervisor logs (v0.1.3)

The default config includes two additional inputs:

| Source | Path on the host |
|---|---|
| Gateway JSONL | `input/gateways/*/gateway-ocsf.jsonl*` |
| Captured supervisor stream | `input/supervisors/*/stderr.log*` |

Docker mounts these below `/work/input`. Keep replicas in separate directories,
and retain rotation segments and checkpoints.

Enable gateway JSONL in its `gateway.toml`, using a writable persistent path:

```toml
[openshell.gateway.ocsf_log]
path = "/path/to/input/gateways/replica-1/gateway-ocsf.jsonl"
rotation = "daily"
max_files = 7
```

Restart the gateway after changing its configuration. Gateway-wide records may
have no sandbox association. This output does not include supervisor OCSF.

For supervisor records, enable `ocsf_json_enabled` with an authorized OpenShell
administrator, then capture the raw platform log stream:

```sh
openshell settings set --global --key ocsf_json_enabled --value true
mkdir -p input/supervisors/sandbox-1
docker logs --follow SUPERVISOR_CONTAINER > input/supervisors/sandbox-1/stderr.log 2>&1
```

Use `podman logs` in place of `docker logs` when appropriate. This command stays
attached; run the exporter separately. The parser selects `OCSF-JSON` lines,
decodes the payload, and ignores shorthand copies. It expects the unwrapped
platform stream, not Docker JSON/CRI storage wrappers or an extra timestamp
prefix. Collect one structured path per producer; do not also ship that supervisor's JSONL files.
A replayed platform-log capture can duplicate events; `correlation.ocsf_event_uid`
retains the native ID for downstream correlation without changing exporter IDs.

Keep `ocsf_schema_version` unset: this exporter validates native 1.8.0 and replay
1.7.0, not the lossy 1.1/1.3 downgrade. Both upstream sinks are best-effort; source
queue loss or deleted runtime logs cannot be recovered by exporter checkpoints.

## Send to an HTTPS receiver

[http.config.yaml](http.config.yaml) reads the original JSONL inputs and sends
**bare OCSF inside CloudEvents `data`**, using a persistent retry queue.
It bypasses normalization and redaction: source content is forwarded as supplied.
For normalized, redacted delivery, copy `processors.openshell` from `config.yaml`
into `http.config.yaml`, then set `service.pipelines.logs.processors` to
`[memory_limiter, openshell]`. Your receiver must read the OCSF record from
`data.original`.

Provision `secrets/destination-token` (raw bearer token) and `secrets/destination-ca.pem`
(the trusted issuing CA), restrict their access, then run:

```sh
export EXPORTER_DESTINATION_ENDPOINT=https://receiver.example.com:8090/events
./bin/openshell-event-exporter validate --config http.config.yaml
./bin/openshell-event-exporter --config http.config.yaml
```

For Docker, use the same run command with the config mount changed to
`-v "$PWD/http.config.yaml:/work/config.yaml:ro"`, and add
`-e EXPORTER_DESTINATION_ENDPOINT -v "$PWD/secrets:/work/secrets:ro"`.
Use a hostname reachable from the container and present in the server certificate.
Docker Desktop can reach a host receiver using `host.docker.internal`; Linux
Docker Engine can add `--add-host host.docker.internal:host-gateway`. The receiver
must listen on a reachable interface. Bearer authentication requires HTTPS.

A 2xx acknowledges a batch; retryable failures may produce duplicates. Consumers should deduplicate by `(source,id)`.
In raw mode, identical source bodies share an ID.
Append a new input record when switching configurations; saved checkpoints do not
replay records already read.
Health and local recovery output alone do not establish successful remote delivery.

Keep one writer per state directory and separate checkpoint, queue and recovery
paths. Restrict access and manage retention; do not clear state to recover space.
Malformed and oversized input can be marked or rejected; inspect recovery records
and exporter logs. The exporter observes evidence; it does not approve policy changes.

## Go package and development

`go install ./cmd/openshell-event-exporter` installs from this checkout. The module
path is `github.com/NVIDIA/OpenShell-Research/projects/tools/openshell-exporter`; a remote
`go install ...@version` needs a public release tagged
`projects/tools/openshell-exporter/v<version>`. No such release is published by this change.
Custom Collectors can import component packages and register their `NewFactory`.

```sh
go mod verify
go vet ./...
go test -race ./...
go build ./cmd/...
```

The module-level `golang.org/x/crypto` finding [GO-2026-5932](https://pkg.go.dev/vuln/GO-2026-5932)
applies to its unmaintained `openpgp` packages and has no fixed version. This
exporter does not compile those packages: `go list -deps ./...` contains no
`golang.org/x/crypto/openpgp` paths. Recheck package reachability if the Go
dependencies change.

The source is extracted from exporter snapshot
`75a41db6be6cc6ad160e3619ad09270d1cdb5b1d`. This distribution retains the Go
implementation and component tests; deployment tooling lives separately.
Preserve LICENSE and NOTICE when redistributing. Synthetic local and container
checks do not qualify live gateway or external application behavior.
