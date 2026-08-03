---
title: Gates
description: Built-in request matching, body inspection, and trusted extensions.
agent_markdown: true
---

# Gates

A gate receives the current immutable `HttpRequest`, one shared `Timeout`, and
its exact typed configuration. It returns a `GateEvaluation` with `proceed`,
terminal `allow`, or terminal `deny` control.

The default registry ships exactly `regex-body` and `request-rules`. Application
registries may add trusted custom gates; the runtime does not treat Python gate
code as hostile or deeply immutable.

- [Regex-body](regex.md)
- [Request-rules](request-rules.md)
- [Custom gates](custom.md)
