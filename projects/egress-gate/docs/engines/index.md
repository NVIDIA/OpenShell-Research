---
title: Engines
description: Understand how Egress Gate engines detect and replace sensitive entities.
agent_markdown: true
---

# Engines

Engines are pluggable processors that inspect request text for configured
entities. Each policy stage selects an engine, supplies its configuration, and
receives detections plus replacement text when replacement is enabled.

Engines do not decide whether Egress Gate allows or denies a request. The
request processor runs the configured stages in order, enforces shared safety
bounds, and applies the policy's final action to their combined results.

Egress Gate includes two integration paths:

- [RegexEngine](regex.md) provides deterministic detection and replacement
  using a deployment-defined pattern catalog.
- [Custom engines](custom.md) integrate another detector, model, SDK, or
  service through Egress Gate's engine contract and registry.

Use the regex engine when the sensitive values have stable, testable formats.
Add a custom engine when detection requires semantics or an external system
that regular expressions cannot provide reliably.
