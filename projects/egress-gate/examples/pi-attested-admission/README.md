# Pi admission without forks

A small, real Pi-powered coding assistant runs in unmodified OpenShell.
Egress Gate approves content **before** it enters the assistant's live history
or Pi's saved session. It can deny text or replace it; a second check at the
network boundary prevents sending an unapproved user/tool context.

This is an application built from Pi's public APIs, not the stock Pi CLI.
It keeps real tools, tool continuations, project instructions, explicit skills,
and manual/automatic compaction. Neither Pi nor OpenShell needs a patch.

## Try it

Prerequisites: **Linux x86_64**, running Docker, Python 3.11+, uv 0.11+,
curl, and a real key for a text-only OpenAI-compatible Chat Completions model.
The checked-in model uses NVIDIA's inference endpoint and requires access to it.
Edit [model.json](model.json) to use another compatible HTTPS endpoint/model.

From this directory:

```sh
cp .env.example .env
# Edit .env: set PI_MODEL_API_KEY and the host address reachable from Docker.
# Edit model.json if using a different endpoint/model.
./demo.sh prepare
```

Preparation downloads checksum-verified OpenShell **0.0.116** binaries, installs
locked Pi **0.85.1** packages in a pinned Node image, and creates local TLS and
configuration under the project's ignored `.workspaces/pi-no-fork/` directory.
No fork clones, Rust build, global Pi install, or existing gateway are needed.
The current launcher is deliberately Linux-only; other platforms are not tested.

Keep these two terminals open:

```sh
# Terminal 1
./demo.sh serve
```

```sh
# Terminal 2
./demo.sh gateway
```

Then create the sandbox and start a session:

```sh
# Terminal 3
./demo.sh setup
./demo.sh launch
```

The gateway is isolated on port **17672**. Egress Gate listens on **50051**
(authenticated middleware gRPC) and **5443** (authenticated admission HTTPS).
Allow access only from this host/sandbox network. TLS certificates last 30 days.
If these ports are occupied, stop the conflicting demo before starting this one.

Every action is inspectable without executing it, loading `.env`, or printing
secrets:

```sh
./demo.sh --print prepare
./demo.sh --print setup
./demo.sh --print launch
```

Printed commands name credential environment variables; OpenShell reads their
values on the host. The generated admission configuration contains a private
bearer token and must remain outside the image and repository.

## What to try

Type these into the running application:

```text
Hello. Briefly describe what you can do.
Please repeat REDACT_THIS.
/history
DENY_THIS
/history
/skill:review
/compact
/history
/exit
```

`DENY_THIS` and `REDACT_THIS` are harmless, literal demonstration markers defined
in [policy.yaml](policy.yaml), not magic Pi/OpenShell features or real secrets.
The first is denied; the second becomes `[REDACTED]` before insertion.
Policy detection is only as good as its configured rules.

The selected project is `/sandbox/project`, copied from [project/](project/).
The same directory scopes Pi's resource loader, tools, and session store.
Its `AGENTS.md` and skill metadata are admitted as system context.
`/skill:review` loads and renders the actual skill before user-message admission.
The skill asks the model to read the real `notes.txt`; that result is admitted
before the next model call. `cwd` is convenient scoping, not an access-control
boundary: OpenShell's filesystem policy supplies that boundary.

Responses and tool progress are buffered, not streamed into the transcript.
`/history` shows only admitted active context. The startup message names the
Pi JSONL file under `/sandbox/sessions`. Compaction retains the latest whole turn;
older **approved** entries remain in the append-only file. Automatic compaction
uses the same summary path at a completed-turn context threshold. Ctrl-C cancels
the current operation. An unfinished tool batch that cannot be safely closed
requires a new session.

## Verify and clean up

```sh
./demo.sh verify
./demo.sh cleanup
```

Verification uses the **real configured model** and can incur several model
calls and normal provider charges. It checks a raw request without a receipt,
deny/redact history, a real skill/tool continuation, and manual and automatic
compaction. It exits unsuccessfully on any missing capability or failed check;
it does not skip checks or substitute a mock model. Deterministic failure and
pending-admission tests live in [app/test/](app/test/).

Cleanup deletes only this demo sandbox and its provider instances/profiles.
**Sandbox files and sessions are deleted and are not recoverable by this script.**
Copy out anything wanted first. Stop the two foreground services with Ctrl-C.
Downloaded artifacts and private host configuration remain in the ignored state
directory for inspection/reuse. Run setup again to create a fresh sandbox.
Changes to model/policy/project files require prepare, a service restart, and a
fresh sandbox (cleanup then setup).

## How the pieces fit

```text
Pi application                  Egress Gate (outside sandbox)
  candidate ------------------> policy: allow / replace / deny
  approved entry <-------------+
       |
       +--> live context + Pi JSONL
       |
  next user/tool context ------> policy + signed receipt
  model request + receipt
       |
       v
OpenShell supervisor ----------> verify actual request + policy
       |                        strip receipt header
       v
attach real provider key --> model
```

[session.ts](app/src/session.ts) is the only owner of writable history. It uses
Pi's model calls, tool implementations, resource loader, summarizer, and
`SessionManager`; it does not instantiate an autonomous `AgentSession` with
unchecked insertion paths. Finalized assistant text and tool calls are admitted
before execution. Tool output, missing-tool/argument/execution errors, rendered
skills, and completed summaries all pass the same boundary.

[admission.ts](app/src/admission.ts) translates these candidates into the existing
Egress Gate schemas. A provider-context replacement is rejected: silently
redacting only the outbound request would leave saved history inconsistent.

[prepare.py](prepare.py) is **trusted host-side operator code**, not the
in-sandbox harness. It provisions policy, TLS, and endpoint-bound provider
profiles. Setup reads the actual sandbox ID from OpenShell and binds the
admission credential to it. The application cannot supply an authoritative
sandbox ID or choose a policy. Upstream credential delivery gives it placeholders,
not the real model/admission secrets. Placeholders are usable capabilities, not
proof of which code used them; the application removes them from child-process
environments as hygiene, not a security boundary.

The service uses standard OpenShell RPCs and verifies the gateway's signed
extension JWT, including the supervisor's sandbox identity. Its additional
HTTPS listener accepts candidates. There is no loopback bridge, custom RPC,
custom OpenShell protobuf field, or second model proxy.

## Honest boundaries

- The local history property holds for this controlled application's write
  paths. It is not protection against a compromised application or arbitrary
  same-authority code rewriting local files.
- Receipts bind the **ordered user/tool text projection**, destination, sandbox,
  policy and expiry—not the full HTTP body, system prompt, assistant history,
  model parameters, or proof that an extension ran. Request policy still checks
  the intercepted body. Receipts are reusable for identical content for up to
  five minutes; service restarts invalidate them.
- One text-only Chat Completions model, sequential tools, and new sessions.
  No TUI/RPC parity, third-party extensions, resume/branching, images, reasoning
  payloads, WebSockets, or model switching. Unsupported content fails closed.
- Redaction can change ordinary text, not executable tool arguments or call
  identifiers. Admitting a tool result cannot reverse tool side effects.
  Bash output is bounded before Pi's unchecked spill-to-file behavior.
- There is one final Egress Gate middleware binding. Do not append another
  middleware that rewrites receipt-covered content afterward.

See the [architecture and evidence guide](../../docs/architecture/admission.md)
for the exact contract and the Dev Note narrative.
