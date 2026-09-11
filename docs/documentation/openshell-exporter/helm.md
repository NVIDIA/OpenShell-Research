---
title: Deploy with Helm
description: Install the exporter image on Kubernetes using the upstream OpenTelemetry Collector chart.
agent_markdown: true
---

# Deploy with Helm

Use the upstream [OpenTelemetry Collector Helm chart](https://github.com/open-telemetry/opentelemetry-helm-charts/tree/opentelemetry-collector-0.173.0/charts/opentelemetry-collector)
with the exporter image and [helm-values.yaml](helm-values.yaml). Research supplies
configuration; the upstream project maintains the chart. The chart version is
independent of the exporter image version selected in the values file.

You need Helm 4, `kubectl`, access to a Kubernetes 1.24+ cluster with a default
StorageClass, and the gateway/destination credentials described in
[configuration](configuration.md#connect-a-gateway-and-destination). The cluster
must reach those endpoints and pull the image. See the [release status](index.md).
AMD64 and ARM64 Linux nodes use the same image reference; the node's container
runtime selects the matching manifest. An Apple Silicon development cluster
running Linux ARM64 nodes uses the ARM64 image.

## Prepare

Download the files into your deployment directory:

```sh
export DOCS_URL=https://nvidia.github.io/OpenShell-Research/documentation/openshell-exporter
curl -fL "$DOCS_URL/gateway.yaml" -o config.yaml
curl -fL "$DOCS_URL/helm-values.yaml" -o helm-values.yaml
```

Edit `extraEnvs` in `helm-values.yaml` to select your gateway, stable identity,
workspace, authorized sandbox, and HTTPS destination. Adjust storage requests
and set `spec.storageClassName` in each volume claim if the cluster has no default
StorageClass. Keep one replica for this gateway collection profile.

Create a namespace, a Secret from your existing token files, and the configuration
ConfigMap. Replace the two token file paths:

```sh
kubectl create namespace openshell-observability
kubectl -n openshell-observability create secret generic openshell-exporter-auth \
  --from-file=gateway-token=/path/to/gateway-token \
  --from-file=destination-token=/path/to/destination-token
kubectl -n openshell-observability create configmap openshell-exporter-config \
  --from-file=relay=config.yaml
```

The upstream chart expects the configuration key to be named `relay`; this does
not enable NeMo Relay. For private CAs or mTLS, include the corresponding files in
the Secret and set `/run/secrets/...` TLS paths in `config.yaml` before creating
the ConfigMap.

## Install and verify

```sh
helm repo add open-telemetry https://open-telemetry.github.io/opentelemetry-helm-charts
helm repo update open-telemetry
helm upgrade --install openshell-exporter open-telemetry/opentelemetry-collector \
  --version 0.173.0 --namespace openshell-observability \
  --values helm-values.yaml --wait --timeout 5m
kubectl -n openshell-observability rollout status statefulset/openshell-exporter
kubectl -n openshell-observability logs openshell-exporter-0 --tail=30
kubectl -n openshell-observability port-forward service/openshell-exporter 23133:13133
```

In another terminal, run `curl --fail http://127.0.0.1:23133/`. Generate authorized
sandbox activity and confirm CloudEvents arrive at your destination. Port
forwarding checks health; it does not prove delivery. The deployment uses separate
persistent claims for state and recovery, runs non-root, and mounts no Kubernetes
API token. It does not install a gateway or cluster-wide log collection.

## Update or remove

After editing configuration, update the ConfigMap and restart the workload:

```sh
kubectl -n openshell-observability create configmap openshell-exporter-config \
  --from-file=relay=config.yaml --dry-run=client -o yaml | kubectl apply -f -
kubectl -n openshell-observability rollout restart statefulset/openshell-exporter
kubectl -n openshell-observability rollout status statefulset/openshell-exporter
```

For an image upgrade, change `image.tag` or `image.digest` in the values file and
repeat `helm upgrade --install`. Keep the previous image and configuration for
rollback. To remove the workload:

```sh
helm uninstall openshell-exporter --namespace openshell-observability
```

The ConfigMap, Secret, namespace, and StatefulSet PVCs remain. Preserve the PVCs
for reinstall/recovery; removing the namespace also removes these resources.
