# Docker Compose

First [build the image](../README.md#build-locally) with `./scripts/build-images.sh`.

Requires Docker with Compose on Linux or Docker Desktop. Apple Silicon runs the
Linux ARM64 image. Run from the project root:

```sh
cp deploy/docker/.env.example deploy/docker/.env
mkdir -p deploy/docker/secrets/source deploy/docker/secrets/destination
```

Edit `.env`: set endpoints, locally built image or your registry digest, source log directory, and distinct
checkpoint, queue, recovery, and optional OTLP queue paths. `DOCKER_PROFILE` is
`core`, `full` (adds Relay), or `complete` (adds native OTLP and forwarded files).

Create source `ca.pem` and either `token`, paired `tls.crt`/`tls.key`, or both.
Create destination `ca.pem`, `token`, and paired client certificates if required.
Extra profile secrets and paths are listed in [.env.example](.env.example).
Grant UID/GID 65532 read access to secrets/source files and write access to state.

```sh
./deploy/deploy.sh docker validate
./deploy/deploy.sh docker up
./deploy/deploy.sh docker status
./deploy/deploy.sh docker logs
./deploy/deploy.sh docker down
```

The container has a read-only root filesystem, dropped capabilities, resource
limits, and loopback monitoring ports. Keep telemetry inputs on isolated,
authenticated networks. Stopping preserves state. Verify actual delivery and
restart replay using [operations](../../docs/operations.md).
