# OpenShell Event Exporter

This is an experimental research example. Keep changes small and documentation
brief. Preserve all runtime features unless the user requests their removal.
The project is a self-contained Go module under `projects/openshell-exporter/`
in OpenShell Research. Run checks from this directory, not the shared Git root.
Existing telemetry scope names are stable identifiers, independent of the Go
module path; preserve them when relocating source.

## Boundaries

- Collect, normalize, redact, correlate, and export authorized evidence only.
  No database, decisions, recommendations, or OpenShell policy mutation.
- Calculate identity before redaction. Preserve unknown fields in the recursively
  redacted original. Mark malformed evidence instead of silently dropping it.
- WatchSandbox is non-resumable. Policy snapshots do not reconstruct history.
- Keep file checkpoints, destination queues, and recovery storage persistent and
  separate. Never delete state automatically or share it between writers.
- Use read-only source credentials, separate destination credentials, verified TLS,
  explicit source allow-lists, and bounded metric labels. Never commit secrets.
- Keep Relay privacy filtering and native OTLP handling separate. The optional
  proxy queue holds unfiltered input. Keep credentials outside agent containers.
- Keep the injector limited to its fixed forwarder contract, authorized creators
  and namespaces, and private volumes; no Kubernetes read token or policy client.

## Work and validation

See [CONTRIBUTING.md](CONTRIBUTING.md) for commands. Keep dependency pins exact.
Run focused tests, then `mise run pre-commit` before committing. Run race tests
for concurrency changes; build after component or manifest changes; scan
dependencies and final images after dependency or image changes.

Receiver, identity, TLS, checkpoint, and replay changes need real-gateway checks.
Durability changes need restart, rotation, queue, and outage tests. Report skipped
external checks honestly; a local fixture is not production qualification.

Use Conventional Commits with DCO sign-off, without agent attribution. Keep runtime
instructions beside the relevant example; shared references live in `docs/`.
