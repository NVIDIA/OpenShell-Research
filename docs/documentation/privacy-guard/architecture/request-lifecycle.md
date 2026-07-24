---
title: Request lifecycle
description: How Privacy Guard prepares policy, runs ordered engines, and applies one request action.
agent_markdown: true
---

# Request lifecycle

`RequestProcessor` owns the complete protobuf-free flow for one text value. It
runs configured entity-processing stages in order, aggregates detections, and
applies the policy action.

The gRPC service owns the surrounding bytes/text and configuration transport
boundaries.

## Policy configuration

The policy supplies:

```yaml
entity_processing:
  stages:
    - name: optional-diagnostic-name
      config:
        engine: regex
        pattern_catalog:
          entities:
            - name: email
              patterns:
                - pattern: '...'
                  confidence: high
        replacement:
          strategy: template
          template: "[{entity}]"
on_detection:
  action: detect
```

`entity_processing` is an object so concrete pipeline-wide settings can be
added later without changing the stage-list shape. V0 defines only `stages`.
The list must be non-empty.

Each `EntityProcessingStage` contains:

- optional `name`, used only as bounded diagnostic provenance
- required `config`, which is the exact concrete configuration model owned by
  the selected engine

`config.engine` is a Pydantic discriminator. After every implementation is
registered, `EngineRegistry.finalize()` constructs a real discriminated union
of their concrete config models. Engine-specific fields and nested replacement
variants therefore validate, serialize, and appear in JSON Schema without a
generic mapping or translation layer.

When a stage name is omitted, Privacy Guard derives a deterministic one-based
label such as `regex[1]`. All resulting diagnostic names must be unique.

## Configuration resolution and preparation

For each evaluation under the current OpenShell protocol, the service:

1. converts the protobuf `Struct` to a mapping
2. validates it through the finalized registry-backed Pydantic model
3. validates each concrete config against its registered implementation and
   injected resources
4. validates the action/replacement compatibility
5. computes a SHA-256 fingerprint of canonical expanded configuration
6. resolves a cached `RequestProcessor`, or constructs the engines and
   processor and adds it to the bounded cache

`ValidateConfig` performs the validation steps without populating this cache.
Preparation is repeatable; cache state is an optimization and never required
for correctness.

There is no separate execution-plan abstraction. The validated stage order
already contains the necessary policy structure, and the prepared processor
privately retains the corresponding ordered engine instances.

## Text input

The service validates the pre-credentials phase and the request body byte
limit before processing. It still validates configuration for an empty body,
then immediately allows that body without invoking an engine.

A non-empty body must decode as strict UTF-8. The decoded `str` is the only
request input passed to `RequestProcessor`; headers, content type, request ID,
target, and protobuf messages do not cross that boundary.

The processor validates both character and encoded-byte bounds before running
the pipeline.

## Ordered stage execution

The processor derives one invocation strategy for the whole pipeline:

| Policy action | Engine strategy |
| --- | --- |
| `detect` | `DETECT` |
| `block` | `DETECT` |
| `replace` | `REPLACE` |

`PolicyAction` is never passed to an engine.

The processor then:

1. creates one monotonic `Timeout`
2. calls each stage exactly once in policy order
3. passes the current text, invocation strategy, and shared timeout to
   `engine.run()`
4. validates intermediate character, byte, and detection limits
5. passes the returned text to the next stage
6. checks the same timeout after the final result

In detect and block mode, the public engine contract requires returned text to
equal that stage's input. In replace mode, each later stage sees the preceding
stage's processed text.

Detection offsets always refer to the input revision seen by the producing
stage. Privacy Guard does not reinterpret earlier offsets after a later stage
changes the text.

If a stage times out, exceeds an execution limit, or fails, its partial text and
detections are discarded. No later stage runs.

## Detection aggregation

After all stages succeed, the processor aggregates detections by:

```text
source stage + entity + confidence representation
```

It does not deduplicate across stages. Two stages may have inspected different
text revisions, and confidence values from different tools are not assumed to
be calibrated.

The aggregate `EntityDetectionSummary` intentionally omits matched text,
surrounding text, offsets, patterns, and raw engine metadata.

## Applying the policy action

The processor owns the final disposition:

| Action | No detections | One or more detections |
| --- | --- | --- |
| `detect` | Allow original body | Allow original body and report detection summaries |
| `block` | Allow original body | Deny with `privacy_guard_blocked` and report detection summaries |
| `replace` | Allow final processed text | Allow final processed text and report detection summaries |

For `replace`, configuration validation requires every stage to advertise
replacement support and to include its valid engine-specific replacement
recipe. A recipe may remain configured but dormant when the action is changed
to detect or block.

Replacement behavior belongs to each engine. For example, `RegexEngine`
selects deterministic non-overlapping matches and renders its constrained
template. A custom engine backed by another tool owns that tool's native
replacement operation. `RequestProcessor` does not reproduce either algorithm.

## Output

`RequestProcessor.process()` returns a `RequestProcessingResult`:

- detect and block-without-detections return `ALLOW` without replacement text
- block-with-detections returns `DENY`, detection summaries, and
  `privacy_guard_blocked`
- replace returns `ALLOW`, the final text, and detection summaries
- timeout or execution-limit exhaustion returns `DENY` with
  `privacy_guard_limit_exceeded` and no partial summaries or replacement

The service leaves the original request bytes untouched for detect and block.
For replace it UTF-8 encodes the final text and sets `has_body=true`, including
when the final text happens to equal the input.

[Back to the architecture overview](index.md)
