---
title: Deploy with Helm
description: Install the exporter on Kubernetes using its own Helm chart from GHCR.
agent_markdown: true
---

# Deploy with Helm

Use the exporter's `openshell-event-exporter` chart, packaged from its maintained
`deploy/kubernetes/chart/` directory and distributed through GHCR. The chart
configures the exporter, credentials, health checks, and persistent storage.
You do not need access to the source repository.

You need Helm 3.22+, `kubectl`, Kubernetes 1.29+ with a default StorageClass,
and an existing HTTPS gateway and CloudEvents destination. The cluster must
reach both endpoints and pull the image. See the [release status](index.md).
The published chart pins the matching multi-platform image by digest. Linux
AMD64 and ARM64 nodes, including Linux ARM64 clusters on Apple Silicon, use the
same chart and image reference.

## Prepare

Download the small values file into your deployment directory:

```sh
export DOCS_URL=https://nvidia.github.io/OpenShell-Research/documentation/openshell-exporter
export CHART=oci://ghcr.io/nvidia/charts/openshell-event-exporter
export VERSION=0.0.5-rc.1
curl -fL "$DOCS_URL/helm-values.yaml" -o helm-values.yaml
```

Edit `source` and `destination` in `helm-values.yaml`: set the gateway URL,
stable gateway identity, workspace, authorized sandbox label selector, and
CloudEvents URL. An empty selector collects all accessible sandboxes. Adjust
storage sizes; set `storageClass` under each persistence entry if needed.
The `stream` profile collects WatchSandbox activity and policy snapshots with
one exporter replica, durable delivery, and local recovery output.

Create the namespace and Secrets from existing raw token and CA certificate
files. Replace the paths below. Tokens must not include the `Bearer` prefix.
Use a CA bundle that verifies each endpoint, including its public trust chain
when applicable:

```sh
kubectl create namespace openshell-observability
kubectl -n openshell-observability create secret generic openshell-exporter-source-auth \
  --from-file=token=/path/to/gateway-token
kubectl -n openshell-observability create secret generic openshell-exporter-source-tls \
  --from-file=ca.crt=/path/to/gateway-ca.pem
kubectl -n openshell-observability create secret generic openshell-exporter-destination-auth \
  --from-file=token=/path/to/destination-token
kubectl -n openshell-observability create secret generic openshell-exporter-destination-tls \
  --from-file=ca.crt=/path/to/destination-ca.pem
```

For mTLS, set `source.mtlsEnabled` or `destination.cloudEvents.mtlsEnabled` to
`true` and add `--from-file=tls.crt=/path/to/client.crt` and
`--from-file=tls.key=/path/to/client.key` to that TLS Secret's creation command.
For a gateway authenticated only by mTLS, also set `source.bearerEnabled: false`
and omit the source token Secret. Keep TLS verification enabled.

## Install and verify

```sh
helm upgrade --install openshell-exporter "$CHART" \
  --version "$VERSION" --namespace openshell-observability \
  --values helm-values.yaml --wait --timeout 5m
kubectl -n openshell-observability rollout status deployment/openshell-exporter
kubectl -n openshell-observability logs deployment/openshell-exporter -c exporter --tail=30
kubectl -n openshell-observability port-forward service/openshell-exporter 23133:13133
```

In another terminal, run `curl --fail http://127.0.0.1:23133/`. Generate authorized
sandbox activity and confirm CloudEvents arrive at your destination. Health
alone does not prove delivery. The chart creates separate queue, recovery, and
checkpoint PVCs, runs non-root, and uses no Kubernetes API token in this profile.
Recovery records append to `/var/lib/openshell-exporter/recovery/openshell-events.json`.
Monitor storage usage and arrange retention; delivery can retry, so destinations
must deduplicate by `(source,id)`.

## Configure, update, or remove

The chart generates Collector configuration from its values. Inspect the full
settings, including the `complete` and `custom` profiles for additional components:

```sh
helm show values "$CHART" --version "$VERSION" > chart-defaults.yaml
```

Keep your overrides in `helm-values.yaml`. Repeat the install command after
editing values; change `VERSION` to a reviewed chart release when upgrading.
After rotating Secret contents, restart the exporter to reload credentials:

```sh
kubectl -n openshell-observability rollout restart deployment/openshell-exporter
kubectl -n openshell-observability rollout status deployment/openshell-exporter
```

To remove the workload:

```sh
helm uninstall openshell-exporter --namespace openshell-observability
```

The chart retains its PVCs; the manually created Secrets and namespace remain.
Preserve them for reinstall/recovery. Deleting the namespace also deletes these
resources. This profile does not install a gateway or collect cluster-wide files.
