---
title: Deploy with Docker
description: Run and manage the exporter with Docker Compose and persistent local directories.
agent_markdown: true
---

# Deploy with Docker

Follow the [quickstart](index.md#quickstart-with-docker) to download
[compose.yaml](compose.yaml) and [config.yaml](config.yaml), then start the image.
Docker Desktop on Intel/Apple Silicon Macs and Docker Engine on Linux AMD64/ARM64
use the same files. Leave `platform` unset so Docker selects the native image.

The [release status](index.md) applies to all image commands on this page.

## Configure and start

The local `.env` records your UID/GID so the exporter can write its bind-mounted
state and output. Use [the configuration guide](configuration.md) to select a file
source or gateway and destination. Credentials go in `secrets/`; `.env` contains
only settings such as endpoints and the image reference.

From the directory containing `compose.yaml`:

```sh
docker compose run --rm exporter validate --config /etc/exporter.yaml
docker compose up -d --force-recreate --wait
docker compose ps
docker compose logs --tail 30 exporter
curl --fail http://127.0.0.1:23133/
```

Confirm real records appear in `output/events.json` and, if configured, at your
destination. The container runs with your UID/GID, a read-only root filesystem,
a 512 MiB memory limit, and health bound to localhost on the host.

| Local path | Purpose |
| --- | --- |
| `config.yaml` | Collector configuration, mounted read-only. |
| `input/` | Authorized JSONL files, mounted read-only. |
| `state/` | Persistent checkpoints and destination queues. |
| `output/` | Recovery records, preserved across restarts. |
| `secrets/` | Gateway/destination credentials and optional TLS files, mounted read-only. |

## Stop, update, or upgrade

```sh
docker compose stop
docker compose start
```

After editing configuration, validate it and use `up -d --force-recreate --wait`
so the container reads the new file. For an image upgrade, set `EXPORTER_IMAGE`
to the released version or digest in `.env`, then:

```sh
docker compose pull
docker compose run --rm exporter validate --config /etc/exporter.yaml
docker compose up -d --force-recreate --wait
```

Keep a copy of the previous configuration and image reference for rollback.
`docker compose down` removes the containers and network; the bind-mounted local
directories remain. Never share writable state with another exporter.
