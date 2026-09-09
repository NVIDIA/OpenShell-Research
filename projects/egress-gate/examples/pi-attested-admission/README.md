# Pi admission without forks

A small, real Pi-powered coding assistant runs in unmodified OpenShell.
Egress Gate approves content **before** it enters the assistant's live history
or Pi's saved session. It can deny text or replace it; a second check at the
network boundary prevents sending an unapproved user/tool context.

This is an application built from Pi's public APIs, not the stock Pi CLI.
It keeps real tools, tool continuations, project instructions, explicit skills,
and manual/automatic compaction. Neither Pi nor OpenShell needs a patch.

## Try it

You need an **existing, authenticated OpenShell gateway** and the `openshell`
CLI configured with its name. Use OpenShell **0.0.116** (the tested protocol
baseline) or a compatible newer release with middleware authentication and
proxy credential delivery. This example does not install or start OpenShell.

Also needed: Bash, Python 3.11+, uv 0.11+, Docker, and a real key for a text-only
Chat Completions model. The simple image workflow assumes your gateway's Docker
driver uses the same Docker daemon as `docker build`. Remote drivers/image
distribution are outside this example. The host no longer has to be Linux;
Linux has been tested, while macOS execution remains unverified.

From this directory:

```sh
cp .env.example .env
# Fill in the existing gateway name, public signing key path, issuer,
# reachable Egress Gate host, and model API key.
# Edit model.json if using a different endpoint/model.
./demo.sh prepare
./demo.sh registration
```

The checked-in model uses NVIDIA's inference endpoint and requires access to it.
Ask your gateway operator for the **public Ed25519 signing key (PEM)** and exact
JWT issuer. These are not the gateway TLS certificate or its private signing
key. Keep the public-key file at the absolute path set in `.env`; the service
reads it on startup.

`EGRESS_GATE_HOST` must resolve to this service from **both gateway and sandbox**:
use a reachable DNS name or IPv4 address, without a scheme or port.
Docker Desktop commonly provides `host.docker.internal`; Linux Docker may need
its bridge address. Do not use `localhost` when callers are in containers.

Preparation builds the pinned Pi **0.85.1** image and writes service TLS,
policy and provider profiles under `../../.workspaces/pi-admission/`.
No fork clones, gateway binaries, gateway keys or gateway configuration are
created. `registration` prints only the middleware entry to add.

Keep Egress Gate running in one terminal:

```sh
./demo.sh serve
```

Have the operator merge the printed entry into the existing gateway's
configuration. Make `tls/ca.crt` readable to the gateway (copy or mount the
public certificate if needed) and adjust `tls_ca_cert_path` in the entry to
that gateway-visible path. Restart the gateway through its usual service
manager to load the registration; coordinate this on a shared gateway.
The demo never edits or restarts it.

In a second terminal, from this directory:

```sh
./demo.sh setup
./demo.sh launch
```

`setup` displays the selected gateway and creates the `pi-admission` sandbox
and its two provider profiles/instances. Reserve those names for this demo.
Egress Gate listens on **50051** (authenticated middleware gRPC) and **5443**
(authenticated admission HTTPS). Restrict access to the gateway/sandbox network.

Every action can print its commands without executing them, loading `.env`,
or printing secrets:

```sh
./demo.sh --print prepare
./demo.sh --print setup
./demo.sh --print launch
```

Print mode uses exported configuration or placeholders because it does not load
`.env`. Real commands use `.env`; credentials are passed by environment-variable
name. The generated `admission.json` contains a private admission token and must
remain outside the image and repository.

**Validation status:** local cross-language integration passes, but the complete
existing-gateway workflow with a valid model key remains to be verified.
`./demo.sh verify` below is that separate real-model acceptance check.

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
Copy out anything wanted first, then stop `serve` with Ctrl-C. The gateway and
its middleware registration are left untouched; the operator can remove the
registration when the demo is no longer needed. Host configuration and the local
Docker image remain for reuse.

For source/model/policy changes, clean up the old demo sandbox, run `prepare`,
restart `serve`, then run `setup`. Valid service certificates are reused for
the same host. After 30 days or a host change, `prepare` generates new service
TLS: install the new CA in the gateway and restart it before setup. Refresh the
public signing-key file if the gateway rotates its key.

The earlier isolated launcher's `.workspaces/pi-no-fork/` directory is no longer
used. Any old isolated gateway must be stopped separately; this launcher does
not manage it.

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
in-sandbox harness. It provisions policy, service TLS, and endpoint-bound provider
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
