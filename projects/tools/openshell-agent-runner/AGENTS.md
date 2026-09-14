# OpenShell Agent Runner

You are working in OpenShell Agent Runner (OAR), an OpenShell Research project
for launching an ephemeral coding agent in an OpenShell sandbox and returning its result.

- Keep OAR orchestration-only. Repository and Git operations belong to the
  sandboxed agent; gateway, provider, workspace, and inference management belong
  to OpenShell. Do not add abstractions for either domain to OAR.
- Preserve OpenShell's concepts, vocabulary, command names, option names, and
  semantics wherever OAR exposes OpenShell behavior. Introduce OAR-specific
  terms only for behavior that OAR owns.

## Implementation

- Prefer the smallest direct change that satisfies a concrete requirement. Do
  not add abstractions, extension points, compatibility layers, or fallbacks for
  hypothetical future needs.
- Keep defensive programming proportionate to realistic failures. Validate
  external inputs and trust boundaries, but do not complicate internal code for
  implausible states already constrained by the system.
- Keep work within the requested outcome. Small, obvious cleanup in code already
  being changed is welcome when it reduces complexity or removes residue; do
  not use it to justify adjacent features, policy changes, or broad refactors.

## Developer workflow

- Run focused tests while iterating. Before handoff, run `make check` and
  `make build` from this directory.
- Keep tests and user documentation synchronized with behavior and contract
  changes.
