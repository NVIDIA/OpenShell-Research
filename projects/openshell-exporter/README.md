# OpenShell Event Exporter

Collect OpenShell events, normalize and redact records, and deliver CloudEvents
or OTLP. This self-contained Go module runs as a local process or a Docker
container, with the full exporter component set. Experimental research software,
provided as-is; use at your own risk.

For other deployment methods and more advanced features, contact
[delgadof@nvidia.com](mailto:delgadof@nvidia.com).

## Quickstart: local process

Use Go 1.26.8 and a Bash-compatible shell, from this project directory. The build
downloads public dependencies. This first run uses a synthetic OCSF record; it
needs no OpenShell gateway, credentials, or model service.

```sh
go build -o bin/openshell-event-exporter ./cmd/openshell-event-exporter
mkdir -p input state output
chmod 700 state output
printf '%s\n' '{"class_uid":4001,"category_uid":4,"activity_id":1,"type_uid":400101,"time":1789120800000,"metadata":{"uid":"quickstart-1"},"message":"OpenShell exporter quickstart"}' > input/example.jsonl
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

Use the same health and output checks. With existing checkpoints the example is
not replayed; append a new record to test more delivery. Stop with
`docker stop openshell-exporter`. Build locally; no published image is required.
Linux AMD64/ARM64 and native macOS ARM64 are build targets; Docker on Apple Silicon
runs the Linux ARM64 image through Docker Desktop.

## Connect real inputs and destinations

For actual OCSF logs, set `receivers.file_log/ocsf.include` in [config.yaml](config.yaml)
to the readable source file glob. In Docker, mount the source directory read-only
and use its container path in the glob. Mount the directory, so rotated files remain
visible. Logs inside an OpenShell sandbox must first be made accessible to the
exporter; this sample does not install a sandbox agent.

[template.config.yaml](template.config.yaml) describes all exporter-specific
settings and included component types, including gateway collection, OTLP, privacy
filtering, and delivery. Select the needed pipelines and mount their credentials
and persistent storage. The template is a reference, not a ready-to-run deployment.
`openshell-event-exporter components` lists the full component set.

For an HTTPS receiver that expects **bare OCSF inside CloudEvents `data`**, use
[http.config.yaml](http.config.yaml). It reads the same input files and sends
CloudEvents batches with a persistent retry queue. This configuration bypasses
normalization and redaction; source content is forwarded as supplied. Provision
`secrets/destination-token` (raw bearer token) and `secrets/destination-ca.pem`
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

For normalized, redacted HTTP delivery, add `openshell` from `config.yaml` to that
pipeline after `memory_limiter`. The receiver must then read the redacted OCSF
record from `data.original`. A 2xx acknowledges a batch; retryable failures retry
and may produce duplicates. Consumers should deduplicate by `(source,id)`.
Raw mode hashes the body for identity, so identical source bodies share an ID.
Append a new input record when switching configurations; saved checkpoints do not
replay records already read.
Health and local recovery output alone do not establish successful remote delivery.

Keep one writer per state directory and separate checkpoint, queue and recovery
paths. Restrict access and manage retention; do not clear state to recover space.
Malformed and oversized input can be marked or rejected; inspect recovery records
and exporter logs. The exporter observes evidence; it does not approve policy changes.

## Go package and development

`go install ./cmd/openshell-event-exporter` installs from this checkout. The module
path is `github.com/NVIDIA/OpenShell-Research/projects/openshell-exporter`; a remote
`go install ...@version` needs a public release tagged
`projects/openshell-exporter/v<version>`. No such release is published by this change.
Custom Collectors can import the component packages and register their `NewFactory`.

```sh
go mod verify
go vet ./...
go test -race ./...
go build ./cmd/...
```

The source is extracted from exporter snapshot
`6a440f05249ac05aa9b9ef8acada4c909ec86bc8`. This distribution retains the Go
implementation and component tests; deployment tooling lives separately.
Preserve LICENSE and NOTICE when redistributing. Synthetic local and container
checks do not qualify live gateway or external application behavior.
