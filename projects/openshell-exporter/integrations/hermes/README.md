# Hermes and NeMo Relay identity

This integration adds trusted OpenShell sandbox context to each Hermes execution before NeMo Relay emits OTLP traces.

## Why a launcher is needed

OpenShell `v0.0.113` reserves `OPENSHELL_*` environment keys and does not inject the sandbox ID into `sandbox exec`. A trusted control plane must resolve the sandbox immediately before execution and pass non-reserved values:

~~~text
HERMES_OPENSHELL_SANDBOX_ID
HERMES_OPENSHELL_SANDBOX_NAME
HERMES_OPENSHELL_POLICY_VERSION
~~~

Do not bake a sandbox ID into an image or reuse a long-lived static configuration.

## Flow

1. Authenticate to the OpenShell gateway.
2. Resolve the target sandbox and current policy version.
3. Pass the returned values to one `sandbox exec` invocation.
4. Run `hermes-correlated.sh`.
5. The launcher validates the values and creates a mode-0600 Relay configuration.
6. Hermes and NeMo Relay emit native session, trace, span, and operation identity.
7. The exporter applies Relay privacy filtering before persistent destination queues.

## Template placeholders

The Relay plugin template must include:

~~~toml
"openshell.sandbox.id" = "__OPENSHELL_SANDBOX_ID__"
"openshell.sandbox.name" = "__OPENSHELL_SANDBOX_NAME__"
"openshell.policy.version" = "__OPENSHELL_POLICY_VERSION__"
~~~

If no policy version is available, the launcher removes that complete line. It never invents a value.

Optional path overrides are:

~~~text
HERMES_NEMO_RELAY_TEMPLATE
HERMES_NEMO_RELAY_RUNTIME
HERMES_BINARY
~~~

## Session identity

NeMo Relay and the Hermes transcript use different session identifiers. When qualification requires an exact mapping, read the ATIF artifact inside the trusted sandbox and extract only the transcript ID plus Relay session-instance ID. ATIF may contain prompts, responses, and tool data; do not export the full artifact.

Sandbox-provided attributes are correlation claims, not authorization. Use authenticated OpenShell evidence or attestation for enforcement.
