# Elastic SOC demo

Optional downstream example: CloudEvents → Logstash/Elasticsearch/Kibana;
Relay OTLP → APM Server; exporter metrics → Prometheus/Grafana. The exporter
remains vendor-neutral and never changes OpenShell policy.

Requires the [base demo](../README.md), an NVIDIA API key in the environment,
at least 4 GiB additional memory, and free loopback ports 3000/5601/9090/9200/18443.
Initial image downloads are large. From `examples/demo/real-gateway`:

```sh
./run-elastic.sh
./show-elastic-password.sh
./verify.sh
./verify-elastic.sh
```

Open [Kibana](https://127.0.0.1:5601) for SOC triage and
[Grafana](http://127.0.0.1:3000) for exporter operations. Trust the CA under
`runtime/tls/` only for this fixture. For a remote host:

```sh
ssh -N -L 5601:127.0.0.1:5601 -L 3000:127.0.0.1:3000 USER@HOST
```

In **OpenShell SOC - Analyst Triage and Investigation**, choose a recent denial,
filter by sandbox and time, then inspect OCSF, lifecycle, policy, logs, and Relay.
Prefer direct trace/session/request IDs; temporal proximity is not causation.
Check **Evidence Coverage and Trust** for gaps and validation before drawing
conclusions. Detection rules are downstream examples, not exporter decisions.

Verification checks replay identity, evidence, privacy filtering, roles, assets,
and health. `./down-elastic.sh` stops the stack and retains Elasticsearch data.
Use the [integration pack](../../../../integrations/elastic/README.md) and
[conformance harness](../../../../conformance/README.md) for external services;
this fixture does not qualify retention, scale, privacy, or production use.
