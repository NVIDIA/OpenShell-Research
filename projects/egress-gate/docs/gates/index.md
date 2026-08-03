---
title: Gates
description: Built-in regex-body behavior and trusted gate extensions.
agent_markdown: true
---

# Gates

A gate receives the current immutable `HttpRequest`, one shared `Timeout`, and
its exact typed configuration. It returns a `GateEvaluation` with `proceed`,
terminal `allow`, or terminal `deny` control.

The default registry currently ships only `regex-body`. `request-rules` is a
planned later built-in. Application registries may add trusted custom gates;
the runtime does not treat Python gate code as hostile or deeply immutable.

- [Regex-body](regex.md)
- [Custom gates](custom.md)
