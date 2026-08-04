---
title: Request lifecycle
description: How one OpenShell evaluation becomes an EgressResult.
agent_markdown: true
---

# Request lifecycle

## 1. Validate the transport

The service checks the pre-credentials phase, exact protobuf configuration,
context, target, header, and body bounds. Domain models then enforce bounded
scalar and aggregate values. Invalid input produces a cataloged gRPC failure.

## 2. Validate and prepare the policy

The service converts the protobuf `Struct` to a mapping. The finalized
`GateRegistry` validates it as an exact `EgressGateConfig`. The registry then
prepares each configured gate and creates a `RequestProcessor`. Preparation
uses one replacement lock and the request `Timeout`. The service publishes the
candidate only after a final deadline check.

## 3. Execute the pipeline

For each configured gate:

1. Check the shared deadline.
2. Evaluate the current immutable `HttpRequest`.
3. Reconstruct and validate the returned `GateEvaluation`.
4. Add a content-safe `GateTrace` and runtime-owned `SourcedFinding` values.
5. On `proceed`, apply the patch to form the next current request.
6. On terminal `allow` or `deny`, stop without invoking later gates.

The processor keeps the original request private. The final allowed patch
combines preceding patches in order. A denied result always has an empty patch.
Body replacement `None` and `b""` remain distinct.

If every gate proceeds, `default_decision` controls the result. Default deny
uses `egress_gate_default_deny`. Default allow has no reason code.

## 4. Handle runtime limits

Deadline expiry, worker-slot exhaustion, mutation bounds, finding limits, and
encoded output limits return an atomic deny with source `runtime_limit` and
`egress_gate_limit_exceeded`. No partial mutations or findings are returned.
Gate contract and execution failures remain gRPC failures.

## 5. Serialize the result

The service converts the protobuf-free `EgressResult` to the current OpenShell
wire contract. It serializes exactly five finding fields and never puts source
or attributes into labels or result metadata. An explicit empty replacement
sets `has_body=true` with an empty body.
