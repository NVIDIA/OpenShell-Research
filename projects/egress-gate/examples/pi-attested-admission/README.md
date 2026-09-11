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

Also needed: Bash, Python 3.11+, uv 0.11+, Docker, and a provider API key with
available credit/quota for a tool-capable Chat Completions model. This demo uses
text input/output, including model reasoning. The simple image workflow
assumes your gateway's Docker
driver uses the same Docker daemon as `docker build`. Remote drivers/image
distribution are outside this example. The same commands below work with a local,
installer-managed gateway on macOS or Linux; the helper selects its config and
service manager automatically. Remote and custom gateway deployments require
operator-managed registration (see below).

For a fresh setup, from `projects/egress-gate/examples/pi-attested-admission/`
(keep your existing `.env` and `models.json` when updating an installed demo):

```sh
openshell gateway list
cp .env.example .env
cp models.json.example models.json
# Edit .env: gateway name, reachable service host, and your provider API key.
# The model template is ready for OpenRouter; edit it only to choose another model/provider.
./demo.sh prepare
```

The template uses [GLM-5.3-Flash on OpenRouter](https://openrouter.ai/z-ai/glm-5.3-flash),
which supports tool calling and always-on reasoning. Put an [OpenRouter API key](https://openrouter.ai/settings/keys)
in `PI_MODEL_API_KEY` in `.env`. No NVIDIA account, inference hub, or
OpenRouter-specific SDK is required. OpenRouter's [Chat Completions API](https://openrouter.ai/docs/quickstart)
uses `https://openrouter.ai/api/v1`; its attribution headers are optional and
are not needed here. Usage is billed by your provider.

Create your own `models.json` from [models.json.example](models.json.example).
It uses **Pi's native `{"providers": {...}}` catalog format**. Add as many providers
and models as you like. One declared model is selected automatically; with more
than one, set `PI_MODEL=provider/model` in `.env` (for example,
`PI_MODEL=openrouter/z-ai/glm-5.3-flash`). Model IDs can contain slashes.
Leave `PI_MODEL` unset for the single-model template. To use another provider,
replace the provider name, HTTPS base URL, model ID and model limits, and supply
that provider's key in the same `PI_MODEL_API_KEY` variable. No script or policy
edits are needed: preparation derives the permitted host and request path from
the selected model. Use a tool-capable model through an
OpenAI-compatible Chat Completions endpoint. Set:

- `providers.<provider>.models`: your models, each with an `id` and optional `name`.
- `providers.<provider>.baseUrl`: the HTTPS API base, such as `https://your-provider.example/v1`;
  the application appends `/chat/completions`.
- `contextWindow` and `maxTokens`: the model's context limit and your desired
  response limit, in tokens. Pi applies its normal context-fit adjustment; the
  application adds no response-token cap. The template uses the model's published
  context window and chooses a 32,768-token output budget, shared by reasoning
  and the answer; adjust it as desired.
- `samplingParams`: Pi forwards model sampling settings such as `temperature`
  and `top_p` without application overrides. The gate's supported request shape
  still applies; unknown provider-specific fields are rejected, not dropped.
- `compat.maxTokensField`: the field your provider accepts (`max_tokens` or
  `max_completion_tokens`). The other compatibility settings are conservative
  defaults; adjust them if your endpoint requires it.

Keep `api` and text-only `input` as shown. Set `reasoning` to match your model.
GLM-5.3-Flash cannot disable thinking; its [documented levels](https://docs.z.ai/guides/vlm/glm-5.3-flash)
are `low`, `high`, and `max`. The template's native Pi `thinkingLevelMap` exposes
those levels. Pi clamps its default `medium` to `high`; its normal thinking
controls can select another supported level. Ordinary requests and compaction
use the selected level.
The zero `cost` values disable cost estimates; provider usage is not free.
Put the API key only in `.env`, never in `models.json`.
Both files are ignored by Git. Preparation copies **only the selected model** and
its provider settings into the image, removing provider `apiKey` configuration.
Pi resolves its defaults and compatibility settings; a model-level `baseUrl` or
`api` takes precedence over the provider setting. Declare both values explicitly
at one of those levels. This POC does not support OAuth or custom headers.
Credentials and model caches stay in memory; no writable `/app/agent/auth.json`
is needed.
Compaction uses Pi's normal summary budget, bounded by the model's `maxTokens`.

Prompt caching stays under Pi's control: the gate accepts its cache keys,
retention fields, and compatibility-generated `cache_control` metadata without
rewriting them. To request Pi's longer retention where supported, optionally set
`PI_CACHE_RETENTION=long` in `.env` or export it before `launch`/`verify`; leave it
unset for Pi's default. No rebuild is needed. Pi disables cache retention for
one-off compaction requests. Redaction and compaction can change prompt content
and therefore cache hits; approval receipts are not added to the prompt.

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
(authenticated admission HTTPS). The gateway must reach the first port and the
sandbox must reach the second at `EGRESS_GATE_HOST`; allow those connections
through the service host's firewall. Restrict access to the gateway/sandbox network.
If setup stops partway through, run `cleanup` before retrying `register` and
`setup`; do not repeatedly import the same provider profiles. Keep `serve`
running until cleanup has removed the gateway registration.

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
real model, including the OpenRouter example. Registration lifecycle tests cover
both supported service managers.
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
Pi's resource loader and tools use that directory; saved sessions live separately
under `/sandbox/sessions`.
Its `AGENTS.md` and skill metadata are admitted as system context.
`/skill:review` loads and renders the actual skill before user-message admission.
The skill asks the model to read the real `notes.txt`; that result is admitted
before the next model call. `cwd` is convenient scoping, not an access-control
boundary: OpenShell's filesystem policy supplies that boundary.

Responses and tool output are buffered until approved, rather than streamed
unchecked into the transcript. Pi still shows activity while waiting.
Reasoning text and its provider replay metadata are admitted before the thinking
block reaches live history or JSONL. On allow, native messages are preserved,
including block order and signatures. Plain reasoning can be redacted;
reasoning carrying signed or structured replay metadata is immutable, so a policy attempting to
redact it denies that candidate instead of breaking replay. Opaque metadata is
inspected as supplied, not decrypted. The same applies
to executable tool-call fields. Text redaction across multiple assistant/user
blocks is rejected when the joined projection cannot identify the original block.
The launch and verification commands pass `--disable-warning=UNDICI-EHPA`
directly to Node to hide only the experimental `EnvHttpProxyAgent` notice;
other warnings and errors remain visible. This does not depend on Docker image
environment variables being inherited by `sandbox exec`.
Use Ctrl+O to expand tool output and `/session` to inspect session information;
Pi saves JSONL under `/sandbox/sessions`.
Preferences changed in the TUI survive `/new` within this running application;
they are not saved across launcher restarts. Provider session-affinity/cache
identity follows Pi's normal behavior, including a new identity for `/new`.
Compaction uses Pi's native retention policy and summary computation; only the
admitted final summary enters history. Older **approved** entries remain in the
append-only file. Automatic compaction uses the same checked path at Pi's
context thresholds, including between tool turns. Esc cancels the current operation. Steering and follow-up inputs are
admitted after skill expansion, before joining the transcript. Drafts and pending
input queues are not approved history. An unfinished tool batch that cannot be
safely closed requires `/new`.
Turning automatic compaction off also disables automatic overflow recovery;
manual `/compact` remains available. Pi can split a long turn, but reports
“Nothing to compact” when the conversation fits its recent-context budget.
Transient chat and summary failures stop the operation rather than automatically
retrying; unchecked provider errors are never appended to history.

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
compaction. The verifier lowers retention thresholds for its short test conversations;
the interactive launcher retains Pi's defaults. It exits unsuccessfully on any missing capability or failed check;
it does not skip checks or substitute a mock model. Deterministic failure and
pending-admission tests live in [pi-harness/test/](pi-harness/test/).

Cleanup deletes only this demo sandbox and its provider instances/profiles,
then removes the registration created by `register` and restarts the gateway.
**Sandbox files and sessions are deleted and are not recoverable by this script.**
Copy out anything wanted first, then stop `serve` with Ctrl-C. Host configuration
and the local Docker image remain for reuse. To remove only the registration
(without deleting the sandbox), use `./demo.sh unregister`. This also works
after a failed registration restart; fix the service problem and retry. If an
operator changed the registration, cleanup refuses to remove it. Older ownership
records without the installed entry also require operator-managed removal.

For source/model/policy changes, clean up the old demo sandbox, run `prepare`,
restart `serve`, then run `register`, `setup`, and `launch`. This rebuild is
required because existing sandboxes continue using their old image. Valid service
certificates are reused for the same host. After 30 days or a host change,
`prepare` generates new service TLS: rerun `register` to reload the new CA before setup (other deployments must
update their gateway-visible CA and restart manually). Refresh the
gateway identity by rerunning `prepare` and restarting `serve` if the gateway
rotates its signing key. Discovery currently expects one published signing key.

## How the pieces fit

[agent.ts](pi-harness/src/agent.ts) approves candidates before publishing message
events; Pi's native session is the only history writer.
[session.ts](pi-harness/src/session.ts) connects that agent to the native TUI,
checked compaction, and guards on unsupported write paths.
[admission.ts](pi-harness/src/admission.ts) calls the external service and
attaches receipts to model requests. OpenShell invokes Egress Gate to verify
those requests before adding real provider credentials.

[prepare.py](prepare.py) runs on the trusted host, not in the sandbox. It owns
policy, TLS and credential provisioning. Setup binds the real sandbox ID.
See the [architecture guide](../../docs/architecture/admission.md) for the data
flow, exact receipt contract and verification evidence.

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
  No RPC mode, third-party extensions, resume/branching, images, WebSockets,
  or model switching. Unsupported content fails closed.
- Redaction can change ordinary text, not executable tool arguments or call
  identifiers. Admitting a tool result cannot reverse tool side effects.
  Bash output is bounded before Pi's unchecked spill-to-file behavior.
- There is one final Egress Gate middleware binding. Do not append another
  middleware that rewrites receipt-covered content afterward.

See the [architecture and evidence guide](../../docs/architecture/admission.md)
for the exact contract and the Dev Note narrative.
