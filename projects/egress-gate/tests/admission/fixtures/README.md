# Pi provider fixture provenance

These payloads were captured on 2026-09-02 from Pi commit
`61500e60394060f2f56a76c61a0067c33988c9f8` through the native stream
adapters' `onPayload` fake-fetch boundary. The capture used the three models
and compatibility settings checked into the attested-admission example. No
provider request was sent.

The strict adapter decisions are deliberate:

- Chat Completions accepts assistant `tool_calls`, tool replies, and the
  `reasoning_content` string emitted when Pi replays Qwen reasoning. That
  reasoning field is preserved for validation but is not projected as message
  text.
- Responses accepts replayed `reasoning`, assistant `message`,
  `function_call`, and `function_call_output` items, including the optional
  reasoning fields present in the captured payload.
- Unknown fields, explicit nulls for optional compatibility fields, image
  inputs, and mixed top-level Chat Completions/Responses shapes remain
  unsupported and fail closed.
