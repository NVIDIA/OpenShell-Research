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
Pi application using the native TUI, not full stock Pi CLI parity.

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

The application supplies an admission-controlled `Agent` through the public
`AgentSessionConfig.agent` SDK seam. Pi's native `InteractiveMode` and session
runtime use that agent. It checks candidates before changing live state or
emitting message events; the native `AgentSession` alone persists those approved
events. It does not depend on late message notifications or remove content
after insertion.

| Candidate | What is admitted before writing |
| --- | --- |
| User / explicit skill | Final text after supported skill rendering |
| Project context | Loaded project instructions and model-visible skill metadata |
| Assistant | Finalized answer/reasoning text, replay metadata and tool calls |
| Tool | Final output, including invalid-argument, missing-tool and execution errors |
| Compaction | Complete summary, for both manual and automatic triggers |

Tool-call fields are inspectable but immutable: attempted executable-argument
redaction fails closed. Reasoning text is admitted along with immutable replay
metadata; plain reasoning can be redacted, but reasoning with signed or structured replay metadata
cannot be changed independently. Allowed native messages retain their block
order, signatures and metadata. Unsupported images/provider state is rejected,
not stored as unchecked sidecars. Tool details and progress are not published;
the TUI receives only admitted final results, with activity indicators while
waiting. Editor drafts and pending input queues are distinct from admitted
conversation history.
Bash uses a bounded public operations wrapper to avoid Pi's output-log spill.

On a denied tool result, the application stops model calls. It submits fixed,
content-free failures for outstanding calls through the same boundary. If those
cannot be admitted, the session stops without claiming crash recovery.
Tool side effects themselves are not reversible by result admission.

Compaction uses Pi's public `compact()` computation and native recent-context
retention, including split turns. Its model requests pass through the
receipt-wrapped stream. The complete returned summary, including file-operation
text, is admitted before the trusted `session_before_compact` extension returns
it. Unchecked `details` are omitted. Failure explicitly cancels; it never falls
through to an unchecked default summary. Denial leaves the preceding context and
file unchanged.

Manual and automatic compaction use that same checked path. Native automatic
compaction also runs between tool turns; when enabled, overflow gets at most one
compact/retry. Disabling it disables automatic overflow recovery, not manual
compaction. Short sessions may have nothing to compact under Pi's retention
budget. Transient failures are not automatically retried, and unchecked provider
errors stay out of history. Old approved entries remain in the append-only JSONL
file.

Pi's synchronous system-prompt rebuilds are staged as private candidates. Public
agent/session state retains the last approved system prompt, including while a
new user candidate is pending or denied. Provider calls use the newly approved
snapshot. In-memory TUI preferences survive `/new`; conversation state and its
provider session identity do not carry over.

One cwd scopes resources, tools and storage. It is not confinement; OpenShell
filesystem policy is. The application is installed outside the writable project
and does not load third-party extensions or implicitly resume saved transcripts.

## Service, identity, and receipts

The service adds one bounded `POST /v1/admission` HTTPS endpoint alongside
ordinary OpenShell middleware gRPC. It reuses the transport-neutral admission
models, fixed Pi shape validation, policy pipeline and receipt authority. Provider validation
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
credential, provider destination and policy; setup reads the actual sandbox ID.
The operator supplies Pi's native `models.json` catalog. Preparation selects one
declared model (using `PI_MODEL=provider/model` when there are several), stages
only that model and its provider settings without provider API-key configuration,
and derives the endpoint policy from it. Pi's own parser resolves model defaults
and compatibility settings. Credential and model-cache stores are in memory;
the image does not need a writable `auth.json`. Changing the selection requires
repreparing and recreating the demo, not live switching.
For local installer-managed gateways, `register` adds the demo entry and restarts
the service; `cleanup` removes it only if it still matches the recorded entry.
Other deployments use operator-managed registration. The demo does not generate
gateway credentials or download OpenShell binaries.
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

Receipts use the internal `agent-attestation.v2` claim format.
Its ephemeral service signing key and five-minute lifetime permit identical
retries, not one-time delivery. Restarting the service invalidates old receipts.

The receipt does **not** sign every byte, system/assistant messages, model
parameters or tool schemas. Those remain subject to the normal request policy.
Final-context replacement is rejected by the application so outbound text cannot
silently diverge from its approved history. There is one final middleware
attachment; adding a later content-mutating middleware breaks that assumption.

## Deliberate POC limits

One text-only OpenAI-compatible Chat Completions model, sequential tools, fresh
sessions and explicit skills. The native TUI supports admitted chat, tool cards,
steering/follow-ups, compaction and `/new`. Direct `!`/`!!` shell execution,
custom extension messages, import/resume, branching, renaming, model switching,
and resource reload are blocked at their public session/runtime entry points.
Shell work through the model's bash tool remains supported.
OpenRouter reasoning requests and replay are supported without a Pi patch.
No RPC mode, arbitrary extensions, images, WebSockets, transport
switching, or crash resume.
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
receipt verification over authenticated gRPC. A pseudo-terminal variant drives
the actual Pi TUI, including tool expansion, manual compaction, denial and
`/new`, then inspects the saved JSONL. Only provider responses are
controlled test data; it does not substitute for live OpenShell acceptance.
The example's `demo.sh verify` is a separate real-model end-to-end acceptance
command, not a simulated demonstration. Its success must be observed, not inferred
from unit tests.

The deterministic integration uses HTTPS admission/provider endpoints and gateway
JWT authentication over a local insecure gRPC channel. It does not exercise
production TLS gRPC startup or a live OpenShell gateway.

**The native-TUI workflow still needs live OpenShell/real-model acceptance.**
Run `demo.sh verify` with a valid provider credential before describing the
deployment as e2e-verified. The verifier deliberately lowers retention thresholds
for its short conversations; the interactive launcher keeps native Pi defaults.
The pinned package/image versions are recorded in the example's package lock,
Dockerfile and middleware manifest. Current validation results belong in the PR,
not a second historical log here.

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
