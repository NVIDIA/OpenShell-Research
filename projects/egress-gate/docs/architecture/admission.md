---
title: Admission without harness forks
description: Application-owned history admission and standard OpenShell egress verification.
agent_markdown: true
---

# Admission without harness forks

The [runnable Pi example](https://github.com/NVIDIA/OpenShell-Research/tree/johnny/pi-attested-admission/projects/egress-gate/examples/pi-attested-admission)
uses published Pi 0.85.1 packages with an existing OpenShell gateway (0.0.116
is the tested protocol baseline). No upstream library,
runtime, protobuf, or CLI patches are required. Its smaller surface is a
Pi-powered application, not stock Pi CLI parity.

## The two boundaries

Network enforcement cannot undo an earlier local-history write. For example,
a tool may return sensitive text; blocking the next model request leaves that
text in the transcript if it was already appended. Conversely, a cooperative
application check alone does not prevent another client making a raw request.

```text
Candidate --> admission policy --> approved history --> next model context
                  |                                       |
                deny                                  signed receipt
                  |                                       |
            no history write                    OpenShell inspects request
                                                          |
                                              verify + request policy
                                                          |
                                                 credential --> model
```

There are two distinct properties:

1. **Local insertion:** the controlled application asks before writing to live
   conversation state or Pi's SessionManager. Only approved/replacement content
   enters history. Transient candidate buffers necessarily exist.
2. **Egress:** an external verifier checks a service-signed receipt against the
   actual intercepted request before OpenShell attaches provider credentials.

A receipt proves service approval of covered content, **not** that a particular
extension ran or that every historical append was checked. A compromised
application or same-authority code can violate local storage integrity.

## One history owner

The application uses Pi's public model stream, resource loader, built-in tools,
summary generator, and session storage. It does not run a second autonomous
AgentSession or depend on late message notifications.

| Candidate | What is admitted before writing |
| --- | --- |
| User / explicit skill | Final text after supported skill rendering |
| Project context | Loaded project instructions and model-visible skill metadata |
| Assistant | Finalized text and tool-call IDs, names, arguments |
| Tool | Final output, including invalid-argument, missing-tool and execution errors |
| Compaction | Complete summary, for both manual and automatic triggers |

Tool-call fields are inspectable but immutable: attempted executable-argument
redaction fails closed. Unsupported images/reasoning/provider state is rejected,
not stored as unchecked sidecars. Tool details and progress are not transcripts.
Bash uses a bounded public operations wrapper to avoid Pi's output-log spill.

On a denied tool result, the application stops model calls. It submits fixed,
content-free failures for outstanding calls through the same boundary. If those
cannot be admitted, the session stops without claiming crash recovery.
Tool side effects themselves are not reversible by result admission.

Compaction keeps the latest whole user turn. Its summary-generation request
needs a fresh receipt; its finished summary needs fresh insertion approval.
Denial leaves the preceding context and file unchanged. Auto compaction runs
between completed turns; overflow gets at most one compact/retry. Old approved
entries remain in the append-only JSONL file.

One cwd scopes resources, tools and storage. It is not confinement; OpenShell
filesystem policy is. The application is installed outside the writable project
and does not load third-party extensions or implicitly resume saved transcripts.

## Service, identity, and receipts

The service adds one bounded `POST /v1/admission` HTTPS endpoint alongside
ordinary OpenShell middleware gRPC. It reuses the transport-neutral admission
models, shape adapters, policy pipeline and receipt authority. Provider validation
supports Chat Completions only and extracts ordered user/tool entries directly;
there is no second normalized model-request representation. Branching, extension
messages and standalone bash-execution envelopes are not admission APIs in this
POC. Bash tool output uses the same tool-result boundary as other tools.

Preparation discovers the existing gateway's public Ed25519 signing key and
issuer using its HTTPS discovery endpoints and the CLI's saved mTLS credentials.
Only the gateway name, reachable service host and model key are supplied by the
operator. Discovery requires one published signing key and refuses plaintext,
cross-origin key URLs and untrusted TLS; browser/edge-login gateways are outside
this POC helper's scope. Host setup generates service TLS, one admission bearer
credential, provider destination and policy; setup reads the actual sandbox ID. It prints
a middleware registration for the operator to install and does not generate
gateway credentials, download OpenShell binaries, or restart the gateway.
The sandbox cannot select its authoritative
identity or submit a policy. The single host-owned identity file is populated
after sandbox creation; until then admission is unavailable. There is no
registration API or new credential broker.

Upstream OpenShell delivers endpoint-bound credential placeholders. Real secrets
stay outside the sandbox. A placeholder is still an application-accessible
capability, not process attestation. Removing it from tool child environments
is hygiene, not isolation from malicious same-authority code.

The gRPC boundary verifies the existing EdDSA extension JWT against the operator's
pinned gateway public key, issuer, audience and token type. HTTP evaluations also
require a supervisor caller whose sandbox ID matches request context.
Both listeners use verified TLS. The gateway advertises only the standard HTTP
middleware contract, not fork-specific harness RPCs.

Before every model call, including tool continuation and summarization, the
application asks approval for the ordered user/tool text projection and sends
the resulting base64url receipt in one `x-egress-admission` header.

At egress the verifier:

1. requires exactly one well-formed receipt;
2. parses the supported provider body and derives the ordered user/tool text;
3. checks signature, key, expiry, sandbox, destination, policy and content hash;
4. runs the configured request gates;
5. rechecks that mutations did not change receipt-covered content; and
6. removes the receipt header before forwarding.

The existing `agent-attestation.v2` wire claim format is retained internally.
Its ephemeral service signing key and five-minute lifetime permit identical
retries, not one-time delivery. Restarting the service invalidates old receipts.

The receipt does **not** sign every byte, system/assistant messages, model
parameters or tool schemas. Those remain subject to the normal request policy.
Final-context replacement is rejected by the application so outbound text cannot
silently diverge from its approved history. There is one final middleware
attachment; adding a later content-mutating middleware breaks that assumption.

## Deliberate POC limits

One text-only OpenAI-compatible Chat Completions model, sequential tools, fresh
sessions and explicit skills. No TUI/RPC parity, arbitrary extensions, reasoning,
images, WebSockets, transport switching, branching, or crash resume.
Network policy allows only the chosen POST model path and separately scopes the
admission endpoint. Unknown shapes fail closed; admission requests do not
recursively require model receipts.

Ordinary HTTP-only Egress Gate remains available with `egress-gate serve`.
Only `--admission-config` selects the receipt-required deployment.

## Evidence and Dev Note narrative

The implementation's deterministic tests cover pending/denied candidates before
both live and durable writes, accepted replacements, real tool continuations,
and the shared manual/auto compaction path. Service tests cover authenticated
caller binding, upstream RPCs, receipts, policy decisions and header removal.
A cross-language integration test also runs the actual Pi serializer and HTTP
admission client against local HTTPS admission and provider endpoints. It checks
redaction, skills, a real read-tool continuation, both compaction paths and
receipt verification over authenticated gRPC. Only provider responses are
controlled test data; it does not substitute for live OpenShell acceptance.
The example's `demo.sh verify` is a separate real-model end-to-end acceptance
command, not a simulated demonstration. Its success must be observed, not inferred
from unit tests. See the PR validation record for the latest executed checks.

Protocol and application validation on **2026-09-09** used:

| Component | Tested pin |
| --- | --- |
| Pi public npm packages | `0.85.1`, exact dependencies and integrity hashes in the example lockfile |
| OpenShell CLI, gateway and supervisor | `0.0.116`, release commit `d1155aa70042d3e2ee49dbfa15346b108b7c1d92`; the launcher now uses the operator's installed runtime |
| Node image | `22.22.2-bookworm-slim@sha256:9f6d5975c7dca860947d3915877f85607946403fc55349f39b4bc3688448bb6e` |
| HTTP client | Undici `8.9.0`; explicit public proxy configuration after loading Pi |

Earlier validation using the now-removed isolated launcher exercised TLS/JWT bootstrap,
endpoint-bound admission credentials, allow/deny/replacement, a real Pi session
denial before history, and rejection of a raw provider request without a receipt.
Pi's actual tool-capable serialized request with a receipt passed the gate and
received HTTP 401 from the real endpoint when deliberately given an invalid test
credential. This establishes the transport seam, **not** successful model output.

**Existing-gateway deployment and real-model acceptance remain unverified.**
Preparation is tested with both DNS and IPv4 service addresses against a local
mTLS discovery server. Local cross-language tests exercise service TLS and
gateway public-key verification.
The host launcher contains no Linux-specific binary bootstrap; Linux tests do
not establish macOS deployment support. The checked-in
verification command passed its bypass/denial checks and then failed at the model
call with that invalid credential; it did not skip ahead. Tool continuations,
skills and compaction have deterministic application coverage but must also pass
that real-model command before describing the whole example as e2e-verified.

A useful Dev Note, **“Gating at the network layer is not enough,”** can follow:

1. A network-denied tool result can still contaminate local history.
2. Move the local decision before the write; show deny and replacement in JSONL.
3. Keep a real agent: tools, skills and compaction all use that one boundary.
4. Demonstrate a raw provider request bypassing the application but being denied
   by OpenShell because it has no approval receipt.
5. Explain the complementary guarantees and honestly show their limits.

The takeaway is not “the network boundary is insufficient security.” It is that
local-history integrity and outbound-request authorization happen at different
times and require different enforcement points. No fork makes the composition
easier to reproduce; it does not make the guarantees stronger by itself.
