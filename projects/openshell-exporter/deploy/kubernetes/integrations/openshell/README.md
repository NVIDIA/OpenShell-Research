# OpenShell Kubernetes integration

The complete Helm profile combines four independent OpenShell lanes:

1. read-only `ListSandboxes` and full public `WatchSandbox` gRPC;
2. automatic per-sandbox OCSF and operational file forwarding;
3. native gateway and compute-driver OTLP traces;
4. namespace-scoped Pod and Event context.

## Automatic sandbox file forwarding

OpenShell creates an Agent Sandbox custom resource before the Agent Sandbox controller creates its Pod. The exporter chart installs a trusted admission webhook for that resource. On an authorized `CREATE`, the webhook adds a fixed Fluent Bit container, private `ReadWriteOncePod` evidence/state claim templates, a ConfigMap, and Secret references to `spec.podTemplate`.

This works with current OpenShell without changing its gateway or controller code. It is deliberately narrow: the webhook cannot call OpenShell, cannot read Kubernetes objects, and cannot change policy or the agent command. It rejects reserved-name or `/var/log` mount collisions instead of overwriting user configuration.

In the default combined supervisor topology, the agent evidence claim captures `/var/log`. In the built-in network-sidecar topology, a separate evidence claim captures the network supervisor's `/var/log`; Fluent Bit reads both. Raw OCSF and operational records reach the exporter with sandbox annotation, source instance, file path, and offset intact.

Enable OCSF JSON output before creating sandboxes:

```sh
openshell settings set --global --key ocsf_json_enabled --value true
```

Label each sandbox namespace, configure its exact gateway ServiceAccount username in the Helm allow-list, create the forwarder Secrets, and install the `complete` profile as described in [../../README.md](../../README.md). The injection is not retroactive.

## Native gateway and driver traces

Merge [gateway-otlp.toml](gateway-otlp.toml) into the OpenShell gateway configuration and update its service DNS name for your release. OpenShell v0.0.113 uses `[openshell.gateway.otlp]` and emits OTLP/gRPC traces. The exporter receiver now requires TLS. Its cert-manager issuer chain must already be trusted by the gateway and any external compute driver because this OpenShell version exposes no per-OTLP CA or client-certificate setting. It therefore provides encrypted server-authenticated TLS, not mTLS. Port `4319` also stays cluster-internal and isolated by NetworkPolicy; never publish it through an Ingress or LoadBalancer.

The gateway setting also reaches supported external compute drivers launched with that endpoint. Kubernetes compute operations use the gateway trace provider. This lane exports traces; it does not replace OCSF files or WatchSandbox.

## NeMo Relay through Provider v2

Use [relay-otlp-provider.yaml.tpl](relay-otlp-provider.yaml.tpl) for the Kubernetes Relay lane. Render `__OPENSHELL_EXPORTER_HOST__` to the exporter Service DNS name, enable `providers_v2_enabled`, lint and import the profile, then create a provider with the real Relay input bearer from the control-plane secret source. Attach that provider to every sandbox that should emit Relay traces.

The OpenShell gateway Kubernetes Secrets credential driver must be enabled in a dedicated namespace with `allowReferenceNamespace: false`. The real bearer is stored there. The sandbox process receives only `NEMO_RELAY_OTLP_TOKEN` as an opaque endpoint-bound placeholder; `hermes-correlated` constructs the Authorization header in process memory, and the OpenShell network proxy substitutes the real value only for HTTPS POST `/v1/traces` to the profile host. Do not log or persist either value.

Provider v2 cannot inject client TLS keys, so configure this receiver with `relay.input.mtlsEnabled: false`, distribute the exporter CA to the sandbox trust store, and enforce NetworkPolicy. This exception is specific to the Provider v2 path; file forwarding remains mTLS plus bearer.

## Future gateway OCSF export

OpenShell issue 2762 proposes a gateway-owned structured OCSF stream. It should eventually be the preferred fan-in, but v0.0.113 has no public protocol to consume. The chart does not invent one. When the API ships, it must be qualified for authentication, identity, backpressure, replay, and gap reporting before replacing the sidecar path.
