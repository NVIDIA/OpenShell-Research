# Kubernetes with Helm

First [build and push to your registry, or load a local image](../README.md).
The Helm chart installs the exporter close to an OpenShell gateway. It can run as one standalone service or as separate edge and central releases.

## Choose a profile

| Profile | Sources |
|---|---|
| `stream` | `ListSandboxes` and non-resumable `WatchSandbox` |
| `complete` | stream, per-sandbox file forwarding, native OTLP, Kubernetes context, and Provider v2 Relay |
| `custom` | explicit acquisition and delivery modules |

Start with [values.complete.example.yaml](chart/values.complete.example.yaml) for the maximum implemented Kubernetes profile. Use [values.custom.example.yaml](chart/values.custom.example.yaml) for least-privilege module selection.

The optional topology roles are:

- `standalone`: acquisition, processing, queues, recovery, and customer delivery in one release;
- `edge`: gateway-local acquisition and processing, then authenticated OTLP delivery to central;
- `central`: authenticated internal intake, correlation, persistent customer queues, and delivery.

Use [values.edge.example.yaml](chart/values.edge.example.yaml) and [values.central.example.yaml](chart/values.central.example.yaml) only after measuring a need for split scaling or trust isolation.

## Complete profile flow

~~~mermaid
flowchart LR
    G[OpenShell gateway] -->|read-only gRPC| W[WatchSandbox]
    G -->|native OTLP| O[Exporter]
    G -->|creates sandbox| A[Fail-closed injector]
    A --> S[Agent Sandbox Pod]
    S -->|files| F[Fluent Bit sidecar]
    F -->|mTLS + bearer OTLP/HTTP| O
    R[NeMo Relay] -->|Provider v2 HTTPS + bearer substitution| O
    K[Kubernetes API] -->|namespace read-only context| O
    O -->|CloudEvents HTTPS| C[Customer receiver]
    O -->|OTLP gRPC or HTTP| T[Telemetry backend]
    O --> Q[(Queues, checkpoints, recovery)]
~~~

## How sandbox files are collected

The complete profile injects one fixed Fluent Bit sidecar into each authorized Agent Sandbox at creation time.

Each sandbox gets private `ReadWriteOncePod` storage for:

- agent evidence files;
- network-supervisor evidence when that container exists;
- Fluent Bit checkpoints and buffered chunks.

There is no shared RWX volume, host path, container-runtime socket, or cross-sandbox mount. Fluent Bit reads files and forwards provenance. The exporter still owns OCSF parsing, stable identity, validation, redaction, correlation, and customer delivery.

The injector mutates only Agent Sandbox `CREATE` requests. It requires:

1. the namespace injection label;
2. membership in `sandboxInjection.allowedNamespaces`;
3. an exact gateway username in `sandboxInjection.allowedGatewayUsernames`.

Existing sandboxes are not changed. Recreate them after enabling injection.

## Relay route

OpenShell Provider v2 stores the real exporter bearer in the gateway credential driver. The sandbox receives an opaque placeholder bound to the approved exporter HTTPS endpoint.

Provider v2 does not inject a client certificate. This route therefore uses verified server TLS, bearer substitution, and NetworkPolicy rather than mTLS. Use the optional [OTLP proxy](../../otlpproxy/README.md) only when a workload-local pre-ingest persistent queue is required.

## Requirements

- Kubernetes 1.29 or newer and Helm 3;
- a user-built Linux AMD64 or ARM64 image, pinned by registry digest or preloaded locally;
- a CSI StorageClass with dynamic `ReadWriteOncePod` support;
- Restricted Pod Security compatible namespaces;
- OpenShell `v0.0.113` with OCSF JSON output enabled;
- read-only gateway credentials and separate customer destination credentials;
- persistent exporter checkpoint, queue, and recovery storage;
- cert-manager or equivalent pre-created certificate Secrets;
- an enforcing CNI for NetworkPolicy;
- Provider v2 and the Kubernetes Secrets credential driver for direct Relay telemetry.

The chart can create cert-manager `Certificate` resources. It does not install cert-manager, create a root CA, create an issuer, or generate bearer tokens.

## Prepare namespaces

~~~sh
kubectl create namespace openshell-observability
kubectl create namespace openshell-sandboxes

kubectl label namespace openshell-observability \
  pod-security.kubernetes.io/enforce=restricted
kubectl label namespace openshell-sandboxes \
  pod-security.kubernetes.io/enforce=restricted \
  observability.openshell.nvidia.com/inject=enabled
~~~

Create source, internal-input, and destination Secrets in their documented namespaces. Keep each trust boundary separate. Every TLS Secret must contain the keys expected by its chart setting; cert-manager-managed TLS Secrets use `ca.crt`, `tls.crt`, and `tls.key`.

Enable OpenShell OCSF JSON output:

~~~sh
openshell settings set --global --key ocsf_json_enabled --value true
~~~

## Configure

~~~sh
cp deploy/kubernetes/chart/values.complete.example.yaml \
  deploy/kubernetes/values.local.yaml
cp deploy/kubernetes/.env.example deploy/kubernetes/.env
~~~

Edit both files. Replace:

- image repositories, tags, and digests;
- gateway endpoint, stable gateway ID, workspace, and selectors;
- source and destination Secret names;
- issuer references and TLS Secret names;
- storage classes and sizes;
- sandbox namespaces and exact gateway ServiceAccount username;
- customer CloudEvents and OTLP endpoints.

Keep direct file mounts disabled when the sidecar forwards the same files.

## Validate and install

~~~sh
./deploy/deploy.sh kubernetes validate
./deploy/deploy.sh kubernetes up
./deploy/deploy.sh kubernetes status
~~~

Validation runs Helm lint and template checks, validates local-image mode or registry digests, rejects insecure or contradictory settings, and checks required Secrets, PVC settings, certificates, and namespace controls.

Create a new sandbox, then confirm injection:

~~~sh
kubectl -n openshell-sandboxes get sandbox,pod,pvc
kubectl -n openshell-sandboxes get pod SANDBOX_POD \
  -o jsonpath='{.spec.containers[*].name}{"\n"}'
~~~

The Pod should contain `openshell-evidence-forwarder` and private evidence/state claims.

## Operate

~~~sh
./deploy/deploy.sh kubernetes logs
./deploy/deploy.sh kubernetes down
~~~

Standalone and edge releases are single stateful writers. Sharded central uses a StatefulSet with private queue and recovery claims per ordinal. Never point multiple Pods at one existing state claim.

For central scaling:

- deploy one tenant per namespace and release;
- scale up only after capacity measurements;
- before scaling down, stop membership changes and verify the removed ordinal queues are empty;
- retain removed PVCs through the rollback window;
- do not use an HPA for central replicas.

Chart-created exporter PVCs use a keep policy. Uninstalling the release does not authorize deleting evidence state. Per-sandbox claims follow the Agent Sandbox lifecycle and the approved retention policy.

## Security summary

The chart uses non-root containers, read-only root filesystems, dropped capabilities, disabled service-account token mounting where possible, namespace-scoped read roles, fail-closed admission, TLS, separate credentials, NetworkPolicy, and private state volumes.

Native OpenShell OTLP uses server-authenticated TLS because the current gateway cannot present a client certificate. Sandbox file forwarding requires mTLS plus bearer authentication. Operations endpoints remain cluster-local unless an operator explicitly publishes them.

## Qualification boundary

Helm rendering proves configuration structure, not production behavior. Before declaring support, test real OpenShell sandboxes for both supervisor topologies, file rotation and truncation, sidecar/exporter/node restarts, destination outages, webhook and cert-manager failures, credential rotation, storage exhaustion, and external CloudEvents and OTLP destinations.

`WatchEvents`, gateway-global evidence, arbitrary host/application logs, and policy mutation remain unavailable or excluded.
