---
title: Deploy with Podman
description: Run the exporter rootlessly on Linux with persistent local directories.
agent_markdown: true
---

# Deploy with Podman

You need rootless Podman on Linux AMD64 or ARM64, `curl`, and a Bash-compatible shell. No Compose
provider is required. The [release status](index.md) applies to these image
commands. Keep local port `23133` free.

On macOS, Podman runs Linux containers through Podman Machine; Apple Silicon uses
the ARM64 image. The commands below target a Linux host. For a Mac quickstart,
use the [Docker Desktop path](index.md#quickstart-with-docker).

## Prepare

```sh
mkdir openshell-exporter && cd openshell-exporter
mkdir input state output secrets
chmod 700 state output secrets
export DOCS_URL=https://nvidia.github.io/OpenShell-Research/documentation/openshell-exporter
curl -fL "$DOCS_URL/config.yaml" -o config.yaml
export EXPORTER_IMAGE=ghcr.io/nvidia/openshell-exporter:v0.0.5-rc.1
podman pull "$EXPORTER_IMAGE"
podman run --rm --userns=keep-id --user "$(id -u):$(id -g)" \
  -v "$PWD/config.yaml:/etc/exporter.yaml:ro,Z" \
  "$EXPORTER_IMAGE" validate --config /etc/exporter.yaml
```

The [starter configuration](config.yaml) reads `input/*.jsonl` and appends
normalized records to `output/events.json`. For a gateway and HTTPS destination,
follow [configuration](configuration.md) first; save the non-secret settings in
`.env` and add `--env-file .env` to validation and startup. Validation for that
profile also needs `-v "$PWD/secrets:/run/secrets:ro,Z"`.

## Start and verify

```sh
podman run -d --name openshell-exporter \
  --userns=keep-id --user "$(id -u):$(id -g)" \
  --read-only --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  --cap-drop ALL --security-opt no-new-privileges --memory 512m \
  -p 127.0.0.1:23133:13133 \
  -v "$PWD/config.yaml:/etc/exporter.yaml:ro,Z" \
  -v "$PWD/input:/input:ro,Z" \
  -v "$PWD/state:/state:Z" \
  -v "$PWD/output:/output:Z" \
  -v "$PWD/secrets:/run/secrets:ro,Z" \
  "$EXPORTER_IMAGE" --config /etc/exporter.yaml
curl --fail --retry 10 --retry-all-errors --retry-delay 1 http://127.0.0.1:23133/
```

Copy authorized OCSF JSONL files into `input/`, or append the synthetic record from
the [quickstart](index.md#quickstart-with-docker). Check `output/events.json` and
`podman logs --tail 30 openshell-exporter`. The `:Z` options label only these
working-directory mounts for SELinux; `keep-id` preserves write access for your
host user. See [Podman's volume and user-namespace options](https://docs.podman.io/en/latest/markdown/podman-run.1.html).

## Stop or change configuration

```sh
podman stop openshell-exporter
podman start openshell-exporter
```

To change configuration or image version, stop and remove the container with
`podman rm openshell-exporter`, then validate and repeat the start command.
Keep `state/` and `output/`; deleting either can lose replay state or recovery
records. Restore `EXPORTER_IMAGE` in a new shell before recreating the container.
