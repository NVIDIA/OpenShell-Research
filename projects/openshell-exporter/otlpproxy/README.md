# OpenShell OTLP Proxy

The OTLP proxy is an optional workload-local bridge for agent traces. Use it when Provider v2 is unavailable or when telemetry needs a persistent queue before the exporter accepts it.

Provider v2 is the preferred Kubernetes route because it keeps the real bearer in the gateway credential driver and avoids an extra sidecar.

## Flow

~~~text
Hermes / NeMo Relay
  -> OTLP/HTTP on 127.0.0.1:4318
OTLP proxy
  -> TLS 1.3 + mTLS + bearer
  -> bounded file-backed queue
OpenShell Event Exporter
  -> Relay identity and privacy processing
  -> customer OTLP destination
~~~

The proxy transports traces only. It does not read OpenShell APIs or files, create CloudEvents, correlate evidence, or mutate policy.

## Minimal distribution

The custom Collector contains only:

- OTLP receiver;
- memory limiter;
- OTLP/HTTP exporter;
- bearer-token authentication;
- file storage;
- health check;
- environment, file, and YAML configuration providers.

`config_test.go` enforces this component allow list.

## Runtime contract

| Surface | Value |
|---|---|
| workload ingress | OTLP/HTTP `127.0.0.1:4318`, 4 MiB maximum request |
| exporter egress | authenticated HTTPS OTLP/HTTP |
| bearer file | `/run/secrets/relay-auth/token` |
| TLS files | `/run/secrets/relay/` |
| minimum TLS | 1.3 |
| queue | file-backed, 2,048 requests, two consumers |
| queue path | `/var/lib/otelcol` |
| health | port 13134 |
| metrics | port 8889 |

The queue is upstream of exporter privacy filtering and may contain prompts, responses, tool data, or credentials supplied by the harness. Use encrypted per-workload storage, restrictive permissions, size limits, monitoring, short retention, and controlled deletion.

## Build and test

~~~sh
mise run build-proxy
mise run container-proxy
go test ./otlpproxy
~~~

Build with `./scripts/build-images.sh proxy` from the project directory. Deploy
the proxy and exporter from the same source version. See [image builds](../deploy/README.md)
for local images and publishing to your own registry.

## Deployment requirements

Run non-root with a read-only root filesystem, dropped capabilities, no privilege escalation, `RuntimeDefault` seccomp, no service-account token, and explicit CPU and memory limits. Keep health and metrics inside the Pod or an approved monitoring namespace.
