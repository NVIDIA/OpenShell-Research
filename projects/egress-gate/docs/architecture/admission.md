---
title: Managed harness admission
description: Pi context admission, whole-context attestations, and egress verification.
agent_markdown: true
---

# Managed harness admission

Managed Pi sessions use the same Egress Gate policy at three checkpoints. The
append and provider-context checkpoints apply policy to supported message
content before it enters history or is sent. Egress verification supplies the
security boundary: a provider request without a valid attestation is denied
before credentials are attached.

| Checkpoint | Pi hook | Result |
| --- | --- | --- |
| History append | `user_message`, `tool_result`, `assistant_message`, `compaction_summary`, `branch_summary`, `extension_message`, or `bash_execution` | Allow, deny, or replace supported content before append |
| Provider context | `provider_context` | Allow, deny, or replace the complete ordered user/tool context and issue an attestation |
| Network egress | OpenShell pre-credentials middleware | Verify the attestation against the provider request, then run request policy |

Assistant append admission covers finalized text and tool calls. Assistant
thinking is not append-admitted or included in the attested user/tool context
hash; request policy scans it at egress. Tool calls are inspectable and
denyable but immutable: a redaction targeting their ID, name, or arguments
fails closed rather than changing them.

Append-time allows do not carry attestations. Immediately before a provider
request, Pi submits the complete context so retries, continuations, compaction,
queued input, and restored sessions do not depend on the newest entry alone.
OpenShell retains the signed attestation and returns only an opaque handle to
the runtime adapter.

## Attestation and verification

An `agent-attestation.v2` claim set binds the canonical context hash and entry
count to the harness and schema versions, middleware binding, policy
fingerprint, sandbox, session and submission identifiers, provider adapter,
provider host and port, signing-key identifier, and issue and expiry times. The
attestation is signed with the Egress Gate instance's ephemeral Ed25519 key and
expires after 300 seconds.

At egress, Egress Gate:

1. requires the network enforcement point, rejects the reserved handle header,
   and requires an attestation;
2. parses the provider request with the selected OpenAI request adapter and
   derives its complete ordered user/tool context;
3. verifies the signature, key, lifetime, trusted context fields, entry count,
   and context hash;
4. runs the configured request gate pipeline; and
5. parses the resulting request again and denies if policy mutation changed the
   attested semantic context.

OpenShell attaches proxy-delivered credentials only after this middleware
allows the request.

## Failures and limits

Admission payloads and replacements are limited to 4 MiB. The middleware
manifest advertises the registered harness, hook, schema, and limit. Image
inputs are not supported by the Pi adapter and fail closed. Provider-context
admission currently runs before Pi's transport-specific history rewrites, so
switching transports with tool history or sending an orphaned tool call can
also fail closed.

Stable admission failures include `admission_contract_invalid` and
`admission_unavailable`. Egress verification failures include
`network_context_invalid`, `reserved_header_present`, `attestation_missing`,
`attestation_malformed`,
`attestation_signature_invalid`, `attestation_key_mismatch`,
`attestation_not_yet_valid`, `attestation_expired`,
`attestation_context_mismatch`, `entry_count_mismatch`,
`context_hash_mismatch`, `provider_shape_unsupported`,
`semantic_mutation_denied`, and `egress_verification_failed`. A configured gate
may instead return its own deny reason.

An Egress Gate started with `--require-agent-attestation` is dedicated to
managed harness traffic: unattested matching provider requests fail closed.
The supervisor-owned loopback bridge requires a per-exec capability delivered
to the launched harness on an inherited file descriptor. The launcher reads and
closes that descriptor and deletes its environment name before Pi starts, so
tool subprocesses do not receive the capability. The token remains in Pi's
memory; a same-user process able to read that memory could copy it, though the
sandbox's process isolation and ptrace restrictions reduce this residual risk.
