# Pi provider fixture provenance

The Chat Completions payloads were captured on 2026-09-02 from Pi commit
`61500e60394060f2f56a76c61a0067c33988c9f8` through the native stream
adapter's `onPayload` fake-fetch boundary. They preserve historical serializer
shapes as regression cases, not the current example's model configuration.
No provider request was sent during capture.

The strict adapter decisions are deliberate:

- Chat Completions accepts assistant `tool_calls`, tool replies, and the
  `reasoning_content` string emitted when Pi replays Qwen reasoning. That
  reasoning field is preserved for validation but is not projected as message
  text.
- Unknown fields, explicit nulls for optional compatibility fields, image
  inputs, and Responses requests are unsupported and fail closed.

The current pinned Pi serializer is exercised directly by
`tests/service/test_http_admission.py`, which runs the real application client
against local admission and provider endpoints. Provider responses are controlled
test data; the runnable example's real-model verification is separate.
