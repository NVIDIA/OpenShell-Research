---
title: "Operations"
description: "OpenShell Event Exporter — operations."
agent_markdown: true
---

# Operations

## Verify delivery

```sh
curl --fail http://127.0.0.1:13133/
curl --fail http://127.0.0.1:8888/metrics
```

Health does not prove delivery. Generate authorized source activity and inspect
one redacted recovery record and its destination copy. Compare `(source,id)`,
file coordinates, validation status, and correlation IDs. Check queue drain and:

- `openshell_exporter_source_configured`, `openshell_exporter_source_observed`
- `openshell_exporter_source_last_success_unixtime`
- `openshell_exporter_stream_gap_warnings`
- `openshell_exporter_delivery_events`
- `openshell_exporter_destination_last_success_unixtime`
- `openshell_exporter_delivery_retryable_failures`
- `openshell_exporter_delivery_permanent_rejections`

Keep metrics and structured process logs private. In an isolated test, interrupt
the destination, produce file evidence, restore it, and verify queue drain and
stable-ID replay after restart. A local fixture does not prove external coverage.

## State and upgrades

Persist source files, checkpoints, separate destination queues, reconciliation
state, and recovery output. Never share a writable state directory between
processes or delete state to clear an alert. Retain rotated files until read.
The OTLP proxy queue contains input before Relay privacy filtering.

For upgrades or rollback, preserve a configuration/state snapshot and image digest,
validate the candidate, stop the old writer, then start one writer with compatible
state. Verify source activity and delivery. Rotate credentials one boundary at a
time and prove the retired credential no longer works.

## Alerts

[Prometheus rules](https://github.com/NVIDIA/OpenShell-Research/blob/main/projects/openshell-exporter/operations/alerts/exporter.yaml) link to these remedies.

### Source inactive

Check enablement, source TLS/auth, selectors, readable advancing files, and any
forwarder. Keep input authenticated; do not inject real credentials into agents.

### Source gap reported

Record the affected interval and preserve warning evidence. Reconcile retained
files where possible. WatchSandbox tails cannot reconstruct unavailable records.

### Stream reconnecting or warning

Check gateway health, read authorization, TLS, selectors, and backpressure.
WatchSandbox has no resume cursor.

### Stale policy reconciliation

Check interval, timeout, read-only credentials, worker activity, and checkpoint
permissions. Partial pagination is incomplete evidence; require a later complete
successful read. Do not delete checkpoints.

### Repeated policy reconciliation failures

Use the failure reason to distinguish RPC, conversion, state-read, and state-write
errors. Repair the dependency and verify freshness recovers.

### Policy reconciliation queue saturation

Check API latency, destination backpressure, workers, memory, and checkpoint
health. Restore capacity and confirm drain before increasing concurrency.

### Destination outage or throttle

Check TLS, credentials, response codes, queue use, and disk. Restore the
destination and verify drain. See [response handling](event-model.md#http-delivery).

### Queue enqueue failure

Restore queue storage or destination capacity without deleting state. Retain source
files and reconcile recovery and destination IDs after restart.

### Durable storage unavailable or low

Repair mounts or add capacity. Preserve checkpoints, queues, and recovery. Verify
storage health, advancing checkpoints, and queue drain before resolving the alert.

### Stale checkpoint state

Check recent file activity, write permissions, and single-writer ownership.
Confirm new records advance state; do not recreate the checkpoint.

### Storage state scan truncated

Check unexpected file growth or shared directories. Free-space metrics remain
usable; the bounded scan may miss the newest state entry.

Report issues with versions, redacted config/logs, UTC interval, and storage/queue
status. Never include credentials or raw evidence. See [security](https://github.com/NVIDIA/OpenShell-Research/blob/main/projects/openshell-exporter/SECURITY.md).
