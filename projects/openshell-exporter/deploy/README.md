# Build and deploy

Images are built from source. No project-published exporter images are required.
Base images and third-party components still come from their pinned upstream registries.
Run commands from the project directory.

## Build locally

Requires Docker with Buildx, or Podman 5.x. The first build downloads dependencies.

```sh
./scripts/build-images.sh
# Optional proxy, or both images:
./scripts/build-images.sh proxy
./scripts/build-images.sh all --engine podman
```

The defaults produce `localhost/openshell-event-exporter:v0.0.4` and, when selected,
`localhost/openshell-otlp-proxy:v0.0.4`. The version comes from `VERSION`; the
platform defaults to the host architecture. Use `--platform linux/amd64` or
`linux/arm64` when targeting another architecture. Local builds never push.
The script records source revision and version in image labels; a modified
checkout is labeled `-dirty`. Run `--help` for options.

Docker and Podman [.env examples](docker/.env.example) select the local image
with `OPENSHELL_EXPORTER_PULL_POLICY=never`. Build using the same engine and user
that will run the deployment. A missing local image fails without pulling.

## Shared Kubernetes or your own registry

Authenticate to your registry, then explicitly build and push there:

```sh
docker login registry.example.com
./scripts/build-images.sh all --registry registry.example.com/my-team --push \
  --platform linux/amd64,linux/arm64
```

Docker publication needs `jq`; multi-platform builds need a capable Buildx builder. Podman supports a
single target platform per invocation here. The command prints digest-pinned
references after successful publication; use those in your deployment.
`--push` requires an explicit registry. No registry credentials are stored by the script.

For Helm, set your configured values to:

```yaml
image:
  local: false
  repository: registry.example.com/my-team/openshell-event-exporter
  digest: sha256:REPLACE_WITH_THE_PRINTED_DIGEST
  pullPolicy: IfNotPresent
  pullSecrets: [registry-access]
```

Create the pull Secret in the deployment namespace, or use your cluster's existing
registry identity. It is applied to both exporter and injector Pods. Build for
all architectures that may schedule them. Install the chart directly from
`deploy/kubernetes/chart`; publishing a Helm chart is unnecessary.

Docker/Podman can also use your registry: set `OPENSHELL_EXPORTER_IMAGE` to the
printed digest reference and `OPENSHELL_EXPORTER_PULL_POLICY=missing` (pull only
if absent) or `always`. Remote pulls require a sha256 digest.

## Local Kubernetes without a registry

Build the exporter, then load it into your cluster:

```sh
./scripts/build-images.sh
minikube image load localhost/openshell-event-exporter:v0.0.4
# For kind instead:
# kind load docker-image localhost/openshell-event-exporter:v0.0.4 --name YOUR_CLUSTER
```

Load it into every eligible node. Use the matching Docker context and cluster
profile; local images must be reloaded when rebuilt or nodes are replaced.
After configuring source, destination, Secrets, and storage in `values.local.yaml`:

```sh
helm upgrade --install exporter deploy/kubernetes/chart \
  --namespace openshell-observability \
  -f deploy/kubernetes/values.local.yaml \
  -f deploy/kubernetes/chart/values.local-image.example.yaml
```

The overlay uses an explicit local tag, empty digest, and `pullPolicy: Never`.
When using `deploy.sh`, copy that image block into your configured values file.

## Configure, run, verify, stop

Choose [Docker](docker/README.md), [Podman](podman/README.md), or
[Kubernetes](kubernetes/README.md), prepare the configuration, then run:

```sh
./deploy/deploy.sh TARGET validate
./deploy/deploy.sh TARGET up
./deploy/deploy.sh TARGET status
./deploy/deploy.sh TARGET logs
./deploy/deploy.sh TARGET down
```

Replace `TARGET` with `docker`, `podman`, or `kubernetes`. Validation checks images,
TLS, credentials, and persistent storage. Keep source and destination credentials
separate and never share writable state between processes. Stopping preserves
state; verify actual delivery using [operations](../docs/operations.md).
