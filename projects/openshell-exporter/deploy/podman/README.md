# Rootless Podman

First [build the image](../README.md#build-locally) with `./scripts/build-images.sh --engine podman`.

Requires Linux, Podman 5.x, cgroup v2, and a delegated user session. From the
project root:

```sh
cp deploy/podman/.env.example deploy/podman/.env
mkdir -p "$HOME/.config/openshell-event-exporter"
mkdir -p "$HOME/.local/state/openshell-event-exporter"
```

Edit `.env` with an locally built image or your registry digest, endpoints, read-only source paths, secrets,
and separate writable checkpoint, queue, and recovery directories. Choose
`PODMAN_PROFILE=core`, `full` (adds Relay), or `complete` (adds native OTLP and
forwarded files). Additional settings are in [.env.example](.env.example).

Supply a source CA and bearer token, client certificate/key, or both. The
destination uses its own CA/token and optional client certificate/key.

```sh
./deploy/deploy.sh podman validate
./deploy/deploy.sh podman up
./deploy/deploy.sh podman status
./deploy/deploy.sh podman logs
./deploy/deploy.sh podman down
```

The launcher uses keep-id mapping, host networking, a read-only root filesystem,
dropped capabilities, and resource limits. Grant the rootless user only required
file access; do not run as root to bypass permissions. Check the host firewall
before exposing non-loopback inputs. Stopping preserves state; verify restart
and delivery using [operations](../../docs/operations.md).
