---
title: Gates
description: Built-in request matching and trusted custom gates.
agent_markdown: true
---

# Gates

A gate receives a read-only `HttpRequest` snapshot, one shared `Timeout`, and
its exact typed configuration. It cannot change that request object in place.
To change the request, the gate returns `proceed` with a `RequestPatch`.

The runtime validates the patch and constructs a new read-only snapshot for the
next gate. It also accumulates the patch that the service will return to
OpenShell. A gate can instead return terminal `allow` or terminal `deny` to stop
the pipeline.

The default registry ships exactly `regex`. Application registries can add
trusted custom gates. The runtime does not isolate trusted Python gate code.

- [Regex gate](regex.md)
- [Custom gates](custom.md)
