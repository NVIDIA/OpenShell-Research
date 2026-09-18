# Projects

This directory contains self-contained OpenShell projects grouped by their
semantic kind. Each project lives under `tools/`, `research/`, or
`use-case-examples/` with its own dependencies, runtime notes, and source
layout.

Adding a project? Start with the [project guidelines](PROJECT_GUIDELINES.md),
including how its location determines the automated review kind.

## Tools

- `openshell-middleware-manager`: `omm` CLI that creates and updates version-matched
  Python and Rust OpenShell supervisor middleware projects.
- `openshell-agent-runner`: `oar` CLI for launching ephemeral agents in
  OpenShell sandboxes.
- `openshell-exporter`: OpenShell event collector, normalizer, redactor, and
  CloudEvents or OTLP exporter.

## Research

- `pi-admission`: Pi admission spike demonstrating redaction before history
  writes and signed approval receipts enforced at OpenShell network egress.
- `long-horizon-agent-evals`: Persistent agent experiments over configurable
  time horizons and repeated parallel attempts, starting with GitHub policy
  review.

## Use case examples

These projects are reference implementations for realistic OpenShell use cases.
They demonstrate end-to-end patterns that readers can understand and adapt;
they are not presented as production-ready applications.

- `reachy-mini-openshell`: Reachy Mini conversation demo for OpenShell.
- `robotics-policy-prover`: Robotics demonstration of policy-proving
  agent-generated actions before execution.
