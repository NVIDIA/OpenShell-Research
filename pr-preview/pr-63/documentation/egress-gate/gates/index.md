---
title: Gates
description: Built-in request matching and trusted custom gates.
agent_markdown: true
---

# Gates

A gate receives a read-only `HttpRequest` snapshot, one shared `Timeout`, and
its exact typed configuration. It cannot change that request object in place.
To change the request, the gate returns `proceed` with a `RequestMutations`
value.

The pipeline processor validates the mutations and constructs a new read-only
snapshot for the next gate. It also accumulates the mutations that the Egress
Gate service adapter will map to `HttpRequestResult` if the pipeline allows the
request. The OpenShell supervisor then applies those final mutations to the
intercepted request. A gate can instead return terminal `allow` or terminal
`deny` to stop the pipeline.

The default registry ships exactly `regex`. Application registries can add
trusted custom gates. Egress Gate does not isolate trusted Python gate code.

- [Regex gate](regex.md)
- [Custom gates](custom.md)
