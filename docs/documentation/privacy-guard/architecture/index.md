---
title: Privacy Guard architecture
description: System structure, component boundaries, and request flow for Privacy Guard.
agent_markdown: true
---

# Privacy Guard architecture

Privacy Guard is OpenShell middleware that scans provider-bound HTTP request
bodies and applies policy to detected sensitive data. OpenShell calls it before
adding provider credentials.

Privacy Guard can allow the request unchanged, allow it with a replacement body,
or deny it. It never sends the provider request itself.

## Where Privacy Guard runs

```text
Sandbox process
      |
      | provider-bound HTTP request
      v
OpenShell supervisor
      |
      | gRPC: SupervisorMiddleware
      v
Privacy Guard
      |
      | allow, replace body, or deny
      v
OpenShell supervisor
      |
      | credentials added only after middleware allows
      v
Provider
```

Privacy Guard implements the `SupervisorMiddleware` gRPC service. Its manifest
registers one binding: HTTP requests in the pre-credentials phase.

## Component boundaries

Source paths on these pages are relative to
`projects/privacy-guard/src/privacy_guard/`.

- `service/` owns gRPC, protobuf conversion, worker scheduling, and finding
  aggregation. It does not scan or apply policy. Outside generated `bindings/`,
  no other package imports gRPC or generated bindings.
- `processor.py` coordinates format handlers and scanners, applies policy, and
  redacts bodies. It does not import gRPC or protobuf.
- `request_body/` parses and reconstructs bodies. It does not scan or apply
  policy.
- `scanners/` detects values within one text block. It does not depend on
  request formats, policy, gRPC, or protobuf.
- `config.py` defines policy configuration, not scanner configuration.
- `payloads/` defines transport-independent request and result records.

## Request flow

```text
HttpRequestEvaluation protobuf
        |
        v
PrivacyGuardMiddleware
  validates transport input
  parses policy
  creates InterceptedRequest
        |
        v
RequestProcessor
  selects FormatHandler
        |
        v
FormatHandler.normalize
  returns RequestBody + TextBlock values
        |
        v
Scanner.scan
  returns block-relative Finding values
        |
        v
RequestProcessor
  validates and filters findings
  observes, blocks, or redacts
        |
        +-- policy deny or pre-reconstruction limit --> ProcessingResult
        |
        +-- continue
                |
                v
        FormatHandler.reconstruct
                |
                v
        output-size check
                |
                v
        ProcessingResult
```

Both paths return a `ProcessingResult` to `PrivacyGuardMiddleware`, which
aggregates findings and creates the `HttpRequestResult` protobuf.

The processor is synchronous. The service runs it in a dedicated thread pool
so scanner work does not block the gRPC event loop.

## Core data types

| Type | Meaning |
| --- | --- |
| `PolicyConfig` | Body format and action to apply to selected findings |
| `InterceptedRequest` | Protobuf-free request body, request ID, content type, and parsed policy |
| `RequestBody` | Handler-owned parsed state, original bytes, and text blocks |
| `TextBlock` | One string to scan, with an opaque path and replacement flag |
| `Finding` | One scanner result with block-relative offsets |
| `RequestBodyFinding` | A finding paired with its text-block path |
| `ProcessingResult` | Allow or deny decision, optional replacement body, findings, and reason code |

Domain model fields are strict and frozen. `RequestBody.parsed_value` is
handler-owned and may contain mutable objects; reconstruction must not mutate
it. Fields containing request content are excluded from normal representations.

## Read next

- [Request lifecycle](request-lifecycle.md) explains normalization, scanning,
  policy filtering, and redaction.
- [Scanners](scanners.md) defines the scanner extension contract.
- [Format handlers](format-handlers.md) defines body parsing and reconstruction.
- [Service boundary](service-boundary.md) covers gRPC adaptation and concurrency.
- [Safety and limits](safety-and-limits.md) records failure behavior and resource
  bounds.
