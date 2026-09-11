# Automatic sandbox-local Fluent Bit forwarder

The Helm `complete` profile uses this as the primary Kubernetes file path. A trusted admission webhook adds one fixed Fluent Bit sidecar to every authorized OpenShell Agent Sandbox at creation time. Operators do not merge a Pod fragment by hand, and no shared RWX claim is used.

Fluent Bit owns only the source-side transport:

- always tail agent OCSF and operational files;
- tail network-supervisor OCSF and operational files only when that container and its private evidence mount exist;
- persist a SQLite offset database and filesystem retry chunks on a private `ReadWriteOncePod` claim;
- preserve the raw line, file path, and byte offset;
- attach gateway, workspace, sandbox, acquisition-kind, and source-instance context;
- send OTLP/HTTP to the local exporter with TLS verification, client authentication, bearer authentication, compression, and unlimited retry.

The exporter owns parsing, structural OCSF validation, stable pre-redaction identity, recursive redaction, correlation, CloudEvents construction, customer delivery, and recovery output. Fluent Bit never sends directly to a SIEM and never filters a record because it cannot parse JSON.

The reviewed configurations are [fluent-bit.conf](../../chart/files/fluent-bit.conf) for agent-only Sandboxes and [fluent-bit-network.conf](../../chart/files/fluent-bit-network.conf) for Sandboxes with `openshell-supervisor-network`. The injector chooses from the actual container topology; it never asks an agent-only forwarder to scan an unmounted network path. Each configuration emits one stable source-capability CloudEvent. The agent-only event explicitly marks the network-file lane unavailable with reason `network_container_absent`; this is a coverage diagnostic, not a delivery failure. Do not tail any of these files with a second collector.

Required Secrets in every allow-listed sandbox namespace:

- `openshell-ocsf-forwarder-auth`: `token`, at least 32 random bytes and matching the exporter's forwarded-file receiver token;
- `openshell-ocsf-forwarder-tls`: `ca.crt`, `tls.crt`, and `tls.key`.

When `certManager.enabled` is true, the chart creates and renews the TLS client Secret through a namespace-local `Certificate`. The bearer Secret remains operator managed. Use a dedicated CA-providing issuer; the chart never generates credentials or installs cert-manager itself.

The sidecar is injected only when namespace label, namespace allow-list, and exact gateway admission identity all match. Existing sandboxes must be recreated. See the [Helm guide](../../README.md) for the complete trust, storage, and qualification contract.
