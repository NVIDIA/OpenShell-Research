# Kubernetes gateway and SOC demo

This Omnistation demo runs the complete Kubernetes collection profile with a real OpenShell Agent Sandbox, Hermes, NeMo Relay, Nemotron, per-sandbox file forwarding, policy evidence, Elastic SOC dashboards, and Grafana operations.

~~~text
Hermes + Relay in Agent Sandbox
  -> Provider v2 OTLP -> exporter
Sandbox OCSF and operational files
  -> injected Fluent Bit -> exporter
OpenShell gateway
  -> WatchSandbox and read-only policy APIs -> exporter
Exporter
  -> CloudEvents -> Logstash -> Elasticsearch -> Kibana
  -> OTLP -> APM Server -> Elasticsearch traces
  -> metrics -> Prometheus -> Grafana
~~~

## What it proves

- a real gateway creates and controls the sandbox;
- Provider v2 keeps the real Relay token in the gateway credential driver;
- every authorized sandbox gets one fixed file-forwarder sidecar and private state;
- OCSF, logs, lifecycle, platform events, warnings, policy snapshots, and Relay traces reach the exporter;
- CloudEvents and OTLP use separate destinations and persistent queues;
- Kibana correlates security evidence and agent intent;
- Grafana shows exporter data-plane health;
- the exporter never approves or applies policy.

The demo may create a pending Policy Advisor draft chunk. It observes the emitted evidence and leaves the chunk for human review.

## Requirements

The Omnistation needs Docker, Buildx, at least 8 GiB free memory, enough disk for Minikube plus the SOC stack, and outbound access to pinned registries.

Set the NVIDIA key only in the launching shell:

~~~sh
export NVIDIA_API_KEY="nvapi-..."
~~~

The script stores it in an ephemeral Kubernetes Secret for the control Job. It is not committed or added to exporter evidence.

## Run

~~~sh
cd examples/demo/kubernetes
./run.sh
~~~

The first run installs pinned local tools, starts Minikube, builds and imports images, installs OpenShell and the exporter Helm chart, provisions the SOC stack, runs a real agent task, and verifies the evidence path.

A pass requires real OpenShell evidence, privacy-controlled Relay traces, correlation, a pending policy chunk when supported by the gateway behavior, and authoritative OCSF policy evidence.

## Run more activity

Run another task:

~~~sh
./run-task.sh
./verify.sh
~~~

Create three additional sandboxes:

~~~sh
./create-sandboxes.sh 3 soc-agent
~~~

Open Hermes in a new sandbox:

~~~sh
./hermes-terminal.sh
~~~

Or enter an existing demo sandbox:

~~~sh
./hermes-terminal.sh SANDBOX_NAME
~~~

Inspect cluster and workload state:

~~~sh
./status.sh
~~~

## View from a Mac

Keep this tunnel running on the Mac:

~~~sh
ssh -N \
  -L 15601:127.0.0.1:15601 \
  -L 13000:127.0.0.1:13000 \
  USER@OMNISTATION
~~~

Get the generated Kibana login on the Omnistation:

~~~sh
./show-elastic-password.sh
~~~

Open:

- Kibana SOC: [https://127.0.0.1:15601](https://127.0.0.1:15601)
- Grafana operations: [http://127.0.0.1:13000](http://127.0.0.1:13000)

The browser certificate uses a private demo CA. Do not reuse it outside this cluster.

## Analyst workflow

1. Open the analyst-triage dashboard.
2. Select a denial or evidence-gap alert.
3. Filter on the sandbox ID and alert time.
4. Reconstruct agent intent, OpenShell enforcement, policy context, and source coverage.
5. Distinguish direct-ID joins from temporal context.
6. Preserve stable IDs and redacted provenance in the case.
7. Confirm the current sandbox and policy state before deciding what happened.

## Stop

~~~sh
./down.sh
~~~

Stop and uninstall demo workloads while retaining PVCs:

~~~sh
./down.sh --uninstall
~~~

Review retention requirements before deleting any retained exporter, Elastic, or per-sandbox evidence volume.

## Privacy and qualification boundary

The dashboards receive maximum authorized context but the default Relay profile excludes prompts, responses, tool arguments, tool results, and credential-like attributes. The direct Provider v2 path has no pre-ingest persistent queue; accepted telemetry is privacy-filtered before exporter destination queues.

This is a local integration showcase, not a production qualification. Real-cluster restart, storage exhaustion, credential rotation, external destinations, long-duration capacity, and independent security/privacy gates remain separate work.
