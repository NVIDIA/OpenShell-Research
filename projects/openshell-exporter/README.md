# OpenShell Event Exporter

Send OpenShell security events, sandbox activity, policy snapshots, and agent
traces to your security and observability tools. The exporter preserves source
context, redacts known secrets, and delivers CloudEvents over HTTPS or native OTLP.

Experimental research example, provided as-is. Use at your own risk.

## Quick start

Follow the [quick start](docs/index.md#quick-start-see-real-evidence-locally)
to run a real agent task and view its exported evidence.

## Connect your environment

| What you want | Where to start |
|---|---|
| Build an image or push to your own registry | [Image builds](deploy/README.md) |
| Collect from your existing gateway | [Docker setup](deploy/docker/README.md) |
| Export OCSF or operational files | [Configuration examples](docs/configuration.md) |
| Add Relay or native gateway traces | [Trace configuration](docs/configuration.md#traces-and-privacy) |
| Investigate in Elastic/Kibana | [Local Elastic demo](examples/demo/real-gateway/elastic/README.md) or [integration pack](integrations/elastic/README.md) |
| Run on Kubernetes with sandbox forwarding | [Helm setup](deploy/kubernetes/README.md) |
| Connect your own Hermes agent | [Hermes integration](integrations/hermes/README.md) |
| Run without Docker | [Rootless Podman](deploy/podman/README.md) or [source build](docs/configuration.md#run-a-file-source) |
| Separate collection and delivery | [Edge/central profiles](docs/configuration.md#deployment-profiles) |
| Connect another receiver or OTLP backend | [Event contract](docs/event-model.md) |

The exporter supports stable event identity, recursive redaction, source-gap
reporting, correlation, persistent delivery queues, recovery output, and health
metrics. Enable only the sources you need.

WatchSandbox is non-resumable; policy snapshots are not a complete history.
File replay needs retained source files and persistent state. Receivers must
deduplicate by `(source,id)`. Redacted data remains sensitive, and time-based
correlation is not causal proof. The exporter never makes decisions or changes policy.

[Operations](docs/operations.md) · [Compatibility](docs/compatibility.md) ·
[Release v0.0.4](https://github.com/NVIDIA/OpenShell-Research/releases) ·
[Contributing](CONTRIBUTING.md) · [Releasing](docs/release.md) · [Security](SECURITY.md)

Apache-2.0; retain [LICENSE](LICENSE) and [NOTICE](NOTICE). Contributions require DCO sign-off.
