# Pi admission without forks

A small, real Pi-powered coding assistant runs in unmodified OpenShell.
Egress Gate approves content **before** it enters the assistant's live history
or Pi's saved session. It can deny text or replace it; a second check at the
network boundary prevents sending an unapproved user/tool context.

This launches **Pi's native TUI** through its public SDK: the normal editor,
chat, tool cards, shortcuts, and compaction UI. An application-owned agent
checks content before publishing it to Pi's session. It keeps real tools,
tool continuations, project instructions, explicit skills, and manual/automatic
compaction. Neither Pi nor OpenShell needs a patch.

## Try it

You need an **existing HTTPS/mTLS OpenShell gateway** and the `openshell`
CLI configured with its name. Use OpenShell **0.0.116** (the tested protocol
baseline) or a compatible newer release with middleware authentication and
proxy credential delivery. This example does not install or start OpenShell.

Also needed: Bash, Python 3.11+, uv 0.11+, Docker, and a real key for a text-only
Chat Completions model. The simple image workflow assumes your gateway's Docker
driver uses the same Docker daemon as `docker build`. Remote drivers/image
distribution are outside this example. The same commands below work with a local,
installer-managed gateway on macOS or Linux; the helper selects its config and
service manager automatically. Remote and custom gateway deployments require
operator-managed registration (see below).

From this directory:

```sh
cp .env.example .env
cp models.json.example models.json
# Fill in the gateway name, reachable Egress Gate host, and model API key.
# Edit models.json for your endpoint, model ID, and token limits (see below).
./demo.sh prepare
```

Create your own `models.json` from [models.json.example](models.json.example).
It uses **Pi's native `{"providers": {...}}` catalog format**. Add as many providers
and models as you like. One declared model is selected automatically; with more
than one, set `PI_MODEL=provider/model` in `.env` (for example,
`PI_MODEL=example/YOUR_MODEL_ID`). Model IDs can contain slashes.
No working provider configuration is shipped. Use a text-only, tool-capable
OpenAI-compatible Chat Completions endpoint; NVIDIA inference is one option if
you have access, not a requirement. Set:

- `providers.<provider>.models`: your models, each with an `id` and optional `name`.
- `providers.<provider>.baseUrl`: the HTTPS API base, such as `https://your-provider.example/v1`;
  the application appends `/chat/completions`.
- `contextWindow` and `maxTokens`: the model's context limit and your desired
  response limit, in tokens. The POC caps each response at the smaller of
  `maxTokens` and 4,096 tokens. The template's numbers are examples.
- `compat.maxTokensField`: the field your provider accepts (`max_tokens` or
  `max_completion_tokens`). The other compatibility settings are conservative
  defaults; adjust them if your endpoint requires it.

Keep `api`, `reasoning`, and `input` as shown for this demo.
The zero `cost` values disable cost estimates; provider usage is not free.
Put the API key only in `.env`, never in `models.json`.
Both files are ignored by Git. Preparation copies **only the selected model** and
its provider settings into the image, removing provider `apiKey` configuration.
Pi resolves its defaults and compatibility settings; a model-level `baseUrl` or
`api` takes precedence over the provider setting. Declare both values explicitly
at one of those levels. This POC does not support OAuth or custom headers.
Credentials and model caches stay in memory; no writable `/app/agent/auth.json`
is needed.

If you used the earlier single-object `model.json`, start from the new template
and transfer your endpoint and model settings; renaming the file alone is not enough.
The catalog can contain many models, but each prepared demo uses **one**. To change
the selection, run `./demo.sh cleanup`, stop `serve`, update `PI_MODEL` and its key,
then repeat `prepare`, `serve`, `register`, and `setup`. Live model switching is
disabled because OpenShell's policy and admission receipts are bound to the
prepared endpoint.

`prepare` reads the selected endpoint from `openshell gateway list --output json`
and discovers its issuer and public signing key over verified HTTPS. It reuses
the CLI's existing client certificates under
`${XDG_CONFIG_HOME:-$HOME/.config}/openshell/gateways/<name>/mtls/`.
You do not supply signing keys, an issuer, or certificate paths. This POC supports
registered mTLS gateways; plaintext and browser/edge-login gateways are not
supported by this discovery helper. It never disables TLS verification.

`EGRESS_GATE_HOST` must resolve to this service from **both gateway and sandbox**:
use a reachable DNS name or IPv4 address, without a scheme or port.
`host.docker.internal` may work inside Docker Desktop containers but fail to
resolve for a gateway running directly on the host. Use the service machine's
reachable LAN IPv4 address or a DNS name that works from both places. Do not
use `localhost` when callers are in containers. The gateway connects to the
middleware during startup, so an unreachable address can prevent it starting.

Preparation builds the pinned Pi **0.85.1** image and writes service TLS,
policy and provider profiles under `../../.workspaces/pi-admission/`.
The discovered public key is saved there as `gateway-public.pem`. No fork clones,
gateway binaries or gateway private keys are created.

Keep Egress Gate running in one terminal:

```sh
./demo.sh serve
```

In a second terminal, from this directory:

```sh
./demo.sh register
./demo.sh setup
./demo.sh launch
```

`register` finds the local gateway's config, adds only `pi-egress`, restarts the
gateway through its service manager, and waits for gateway health. It preserves
unrelated settings and refuses to overwrite a registration it did not create.
If the installation uses built-in defaults without a config file, it creates a
minimal one for this registration. You do not need to choose a config path or
run service-manager commands yourself.
**Registration and cleanup briefly interrupt this gateway.** Coordinate this
if anyone else uses it. No OpenShell changes or additional `.env` settings are
needed for the standard installation.

Automatic service handling supports Homebrew and the DEB/RPM user service.
Other service layouts (including Snap and custom config overrides) are
operator-managed; the helper does not guess their config or request root access.

For other deployments, `./demo.sh registration` only **prints** the TOML entry;
it does not install it. The gateway operator must merge it into the active config,
make the public `tls/ca.crt` accessible at `tls_ca_cert_path`, and restart the
gateway. Those operator-managed registrations must also be removed manually.

`setup` displays the selected gateway and creates the `pi-admission` sandbox
and its two provider profiles/instances. Reserve those names for this demo.
Egress Gate listens on **50051** (authenticated middleware gRPC) and **5443**
(authenticated admission HTTPS). Restrict access to the gateway/sandbox network.

Every action can print its commands without executing them, loading `.env`,
or printing secrets:

```sh
./demo.sh --print prepare
./demo.sh --print register
./demo.sh --print setup
./demo.sh --print launch
./demo.sh --print cleanup
```

Print mode uses exported configuration or placeholders because it does not load
`.env`. Real commands use `.env`; credentials are passed by environment-variable
name. The generated `admission.json` contains a private admission token and must
remain outside the image and repository.

**Validation status:** local SDK and native-TUI integration tests pass, including
real admission HTTP/RPC traffic, tool output, compaction, denial and saved JSONL.
Provider responses in those tests are controlled fixtures. The updated native-TUI
workflow still needs acceptance testing with a running OpenShell gateway and a
real model. Registration lifecycle tests cover both supported service managers.
`./demo.sh verify` below is that separate real-model acceptance check.

## What to try

Type these into the running application:

```text
Hello. Briefly describe what you can do.
Please repeat REDACT_THIS.
DENY_THIS
/skill:review
/compact
/new
/quit
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

Responses and tool output are buffered until approved, rather than streamed
unchecked into the transcript. Pi still shows activity while waiting.
The launch and verification commands pass `--disable-warning=UNDICI-EHPA`
directly to Node to hide only the experimental `EnvHttpProxyAgent` notice;
other warnings and errors remain visible. This does not depend on Docker image
environment variables being inherited by `sandbox exec`.
Use Ctrl+O to expand tool output and `/session` to inspect session information;
Pi saves JSONL under `/sandbox/sessions`. The former custom `/history` and
`/exit` commands are gone; use Pi's chat view and `/quit`.
Compaction retains the latest whole turn; older **approved** entries remain in
the append-only file. Automatic compaction
uses the same summary path at Pi's context thresholds, including between tool
turns. Esc cancels the current operation. Steering and follow-up inputs are
admitted after skill expansion, before joining the transcript. Drafts and pending
input queues are not approved history. An unfinished tool batch that cannot be
safely closed requires `/new`.

This POC deliberately blocks `!`/`!!`, resume/import, branching, renaming,
model switching, and resource reload: these need additional handling before
they can be safely enabled. Ask the model to use the **bash tool** for shell
work. Arbitrary extensions are not loaded. Tool cards display admitted results,
not unchecked progress or extra tool metadata such as edit diffs.

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

Cleanup deletes only this demo sandbox and its provider instances/profiles,
then removes the registration created by `register` and restarts the gateway.
**Sandbox files and sessions are deleted and are not recoverable by this script.**
Copy out anything wanted first, then stop `serve` with Ctrl-C. Host configuration
and the local Docker image remain for reuse. To remove only the registration
(without deleting the sandbox), use `./demo.sh unregister`. This also works
after a failed registration restart; fix the service problem and retry.

For source/model/policy changes, clean up the old demo sandbox, run `prepare`,
restart `serve`, then run `register`, `setup`, and `launch`. This rebuild is
also required when upgrading from the earlier line-based interface to the TUI;
launching an existing sandbox continues using its old image. Valid service certificates are
reused for the same host. After 30 days or a host change, `prepare` generates new service
TLS: rerun `register` to reload the new CA before setup (other deployments must
update their gateway-visible CA and restart manually). Refresh the
gateway identity by rerunning `prepare` and restarting `serve` if the gateway
rotates its signing key. Discovery currently expects one published signing key.

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

[agent.ts](app/src/agent.ts) supplies Pi's public `AgentSessionConfig.agent`
with an admission-controlled execution loop. It approves each candidate before
updating live state or emitting message events. Pi's native `AgentSession`
is the **only persistence owner**; it saves those approved events.
[session.ts](app/src/session.ts) wires the runtime and the
`session_before_compact` extension, and blocks alternate unchecked write paths.
Finalized assistant text and tool calls are admitted
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
  The real TUI is used, but not every stock CLI feature is supported.
  No RPC mode, third-party extensions, resume/branching, images, reasoning
  payloads, WebSockets, or model switching. Unsupported content fails closed.
- Redaction can change ordinary text, not executable tool arguments or call
  identifiers. Admitting a tool result cannot reverse tool side effects.
  Bash output is bounded before Pi's unchecked spill-to-file behavior.
- There is one final Egress Gate middleware binding. Do not append another
  middleware that rewrites receipt-covered content afterward.

See the [architecture and evidence guide](../../docs/architecture/admission.md)
for the exact contract and the Dev Note narrative.
