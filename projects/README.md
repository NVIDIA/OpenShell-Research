# Projects

This directory contains self-contained OpenShell projects grouped by their
semantic kind. Each project lives under `tools/`, `research-spikes/`, or
`use-case-examples/` with its own dependencies, runtime notes, and source
layout.

Adding a project? Start with the [project guidelines](PROJECT_GUIDELINES.md),
including how to declare its kind for automated review.

## Tools

- `openshell-middleware-manager`: `omm` CLI that creates and updates version-matched
  Python and Rust OpenShell supervisor middleware projects.
- `openshell-agent-runner`: `oar` CLI for launching ephemeral agents in
  OpenShell sandboxes.
- `openshell-exporter`: OpenShell event collector, normalizer, redactor, and
  CloudEvents or OTLP exporter.

## Research spikes

- `long-horizon-agent-evals`: Persistent agent experiments over configurable
  time horizons and repeated parallel attempts, starting with GitHub policy
  review.

## Use case examples

- `python-project-template`: Minimal, production-ready Python project scaffold
  managed with uv.
- `reachy-mini-openshell`: Reachy Mini conversation demo for OpenShell.
- `robotics-policy-prover`: Robotics demonstration of policy-proving
  agent-generated actions before execution.
