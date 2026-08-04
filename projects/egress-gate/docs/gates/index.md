---
title: Gates
description: Built-in body inspection and trusted custom gates.
agent_markdown: true
---

# Gates

A gate receives the current immutable `HttpRequest`, one shared `Timeout`, and
its exact typed configuration. It returns a `GateEvaluation` with `proceed`,
terminal `allow`, or terminal `deny` control.

The default registry ships exactly `regex-body`. Application registries can add
trusted custom gates. The runtime does not isolate trusted Python gate code.

- [Regex-body](regex.md)
- [Custom gates](custom.md)
