# Real gateway demo

Start with the [quick start](../../../docs/index.md#quick-start-see-real-evidence-locally).
It covers prerequisites, startup, the viewer, verification, and shutdown.
All commands below run from `examples/demo/real-gateway`.

## Customize the task

Set `HERMES_DEMO_PROMPT` before following the quick start to choose the first task:

```sh
export HERMES_DEMO_PROMPT='Inspect /proc/self/status and explain the sandbox limits.'
```

Reports and displayed counts must come from the current real agent execution.
Do not replace a failed source or model with synthetic evidence. Sandbox/time joins
are temporal correlation, not causal proof. The receiver has no policy mutation;
any downstream change needs a separate actuator. This is not external destination certification.
Protect the ignored `runtime/` directory as sensitive data.

## View over SSH

Run this on your own computer, then open the local viewer:

```sh
ssh -N -L 8088:127.0.0.1:8088 USER@HOST
```

The demo CA is `runtime/tls/ca.crt` on the remote host; verify it before trusting it.

## Troubleshoot

| Symptom | Check |
|---|---|
| Preflight fails | Start Docker, enable Compose, and resolve the named missing tool or platform requirement |
| Model call fails | Confirm the API key can access Nemotron 3 Super and the host has outbound connectivity |
| Viewer is unavailable | Wait for `run.sh` to succeed; check port 8088, SSH forwarding, and local CA trust |
| Verification fails | Preserve the report and inspect missing sources, validation errors, and delivery using [operations](../../../docs/operations.md) |

## Optional destinations

For external delivery, set `DEMO_EXTERNAL_DESTINATIONS=true` and configure the
CloudEvents/OTLP URLs, bearer file, and CA. Credentials stay outside the sandbox.
Produce the [conformance projection](../../../conformance/README.md), then run:

```sh
./verify-external.sh /approved/evidence/external-correlated-timeline.json
```

For the local [Elastic demo](elastic/README.md), run `./run-elastic.sh`.

## Stop

`./down.sh` stops services and deletes only the demo-owned sandbox. Gateway data,
OCSF, checkpoints, queues, recovery, and `runtime/` remain. A local run does not
qualify external services, cluster failures, capacity, privacy, or production use.
