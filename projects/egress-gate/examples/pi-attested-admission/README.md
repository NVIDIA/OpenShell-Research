# Pi attested-admission example

This example runs the normal forked Pi CLI inside OpenShell and sends admitted
conversation context to the configured NVIDIA inference endpoint. One
endpoint-scoped provider and credential serve all configured models.

The example demonstrates the same policy at both context boundaries:

- `DENY_THIS` is rejected before Pi adds a user message or tool result to its
  live context.
- `REDACT_THIS` becomes `[REDACTED]` before Pi adds or sends it.

The redaction case makes one real request to your configured endpoint and may
incur charges from that provider.

## Before you start

Use these matching fork branches:

- [Pi `johnny/before-user-message-commit`](https://github.com/johnnygreco/pi/tree/johnny/before-user-message-commit)
- [OpenShell `openshell/pi-egress-admission`](https://github.com/johnnygreco/OpenShell/tree/openshell/pi-egress-admission)
- [OpenShell Research integration branch](https://github.com/NVIDIA/OpenShell-Research/tree/johnny/pi-attested-admission)

You do not need to clone the Pi or OpenShell forks manually. The first
`./demo.sh prepare` clones both into the ignored local workspace
`projects/egress-gate/.workspaces/pi-attested-admission/`. Later runs update
them with fast-forward-only pulls, so the fork contents never appear as
OpenShell Research changes. To reuse a checkout elsewhere, set `PI_REPO` or
`OPENSHELL_REPO` to its absolute path.

The OpenShell gateway needs a running compute backend. On macOS, start Docker
Desktop and wait until `docker info` succeeds before running the gateway;
Podman is also supported. Building the gateway also requires Z3 (`brew install
z3` on macOS or `libz3-dev` on Debian and Ubuntu). The fork recommends `mise`
2026.4.25 or newer.

From the `OpenShell-Research` checkout, change to the example directory. Run
all remaining commands there:

```shell
cd projects/egress-gate/examples/pi-attested-admission
```

Create the local configuration file, replace every example value, and load it
into the current shell:

```shell
cp .env.example .env
# Edit .env before continuing.
set -a
source .env
set +a
```

`set -a` makes assignments loaded by `source .env` available to commands run
from this shell; `set +a` restores the shell's default behavior afterward.

If the model endpoint does not require authentication, set
`PI_MODEL_API_KEY=unused`. Source `.env` in the terminals that run `gateway` and
`reset`; the other actions do not consume the credential.

`PI_WORKSPACE_PATH` is optional. Set it to the absolute path of a project you
want Pi to work on. The reset step uploads its contents to `/sandbox/workspace`
using the project's normal `.gitignore` rules. If you omit it, Pi starts in an
empty `/sandbox/workspace`; no local files are copied. In either case, Pi starts
there, so project instructions, extensions, skills, prompts, and session
grouping follow its ordinary current-directory behavior.

`PI_MODELS_PATH` points to a standard Pi `models.json`. Relative paths are
resolved from this example directory. The checked-in [models.json](models.json)
defines one `attested-provider`, its endpoint and credential reference, and
these models:

| Model ID | Pi transport |
| --- | --- |
| `azure/anthropic/claude-opus-5` | OpenAI Chat Completions |
| `azure/openai/gpt-5.6-sol` | OpenAI Responses |
| `nvidia/qwen/qwen3.8-flash-next` | OpenAI Chat Completions |

Pi starts with the reasoning-capable Qwen model and `high` thinking from
[settings.json](settings.json). Use Pi's normal model picker to switch among all
three without creating another OpenShell provider. Qwen uses Chat Completions
reasoning controls, and GPT-5.6 Sol uses Responses reasoning.
The endpoint's Opus 5 alias currently rejects explicit adaptive-thinking
controls, so it runs with the endpoint's default thinking behavior.

To use another catalog for the same endpoint, copy `models.json`, edit it using
Pi's documented JSON format, and set `PI_MODELS_PATH` to that file. OpenShell
pins network and credential access independently of Pi. To change endpoints,
update the matching host and port explicitly in `models.json`, `policy.yaml`,
and `provider-profile.yaml`.

`EGRESS_GATE_HOST_IP` is the address OpenShell uses to reach Egress Gate on this
machine. It must be a reachable, non-loopback IPv4 address; do not use
`127.0.0.1`. The provider's `baseUrl` in `models.json` is the model endpoint Pi
will call. A model server running on this machine must likewise use a hostname
or address reachable from the sandbox rather than `localhost`.

The example checks in ordinary Pi and OpenShell configuration files. It uploads
`models.json` and `settings.json` unchanged to Pi's standard
`~/.pi/agent` directory. The only generated configuration is a copy of
`gateway-middleware.toml.example` with `EGRESS_GATE_HOST_IP` substituted for its
documented placeholder. If an action needs configuration that is missing, the
script prints the values required by that action and stops before doing work.

Preview the complete workflow before running anything:

```shell
./demo.sh --print all
```

The walkthrough lists the terminal sequence and configuration visible to the
current shell. To inspect the exact commands for one action, use its name—for
example, `./demo.sh --print prepare` or `./demo.sh --print launch`.

## Run the example

Prepare the forks and build and package the locally modified Pi agent core and
coding agent:

```shell
./demo.sh prepare
```

The updates use fast-forward-only pulls and stop instead of merging divergent
local work.

Keep Egress Gate running in one terminal:

```shell title="Terminal 1: Egress Gate"
./demo.sh serve
```

Start the matching OpenShell gateway in a second terminal:

```shell title="Terminal 2: OpenShell gateway"
./demo.sh gateway
```

The example uses its own gateway name and passes it explicitly to every
OpenShell command. It does not depend on or change your globally selected
OpenShell gateway.

After the gateway reports that it is ready, create the demo sandbox from a
third terminal. `reset` is deliberately named: it deletes any prior demo
sandbox and its sessions before uploading the current runtime, configuration,
and workspace.

```shell title="Terminal 3: Pi"
./demo.sh reset
```

Then launch Pi:

```shell title="Terminal 3: Pi"
./demo.sh launch
```

This runs Pi's standard CLI with a trusted runtime extension. Unlike ordinary
user and project extensions, a runtime extension is installed by the launcher,
supplies mandatory runtime boundaries, and is not affected by
`--no-extensions`. Pi still owns argument parsing, the TUI, settings, ordinary
extensions, tools, model selection, compaction, and session storage. `launch`
only enters the existing sandbox; it does not replace the sandbox or Pi's
state. Exit and run `launch` again to use Pi's normal `/resume` flow and
persistent JSONL sessions. Run `reset` only when you intentionally want a fresh
sandbox or need to apply a new runtime, policy, model configuration, credential,
or workspace snapshot.

The sandbox image adds the `fd` and `rg` executables used by Pi's standard
`find` and `grep` tools. Pi itself still comes from the prepared fork package
and starts without restrictive CLI flags. Its standard user and project
resource discovery, extension loading, tools, model picker, thinking controls,
compaction, and session manager remain active. OpenShell's filesystem and
network policy still apply to every process in the sandbox; arbitrary package
downloads are intentionally outside this endpoint-focused example.

The example registers an endpoint-specific provider profile using the host-side
`PI_MODEL_API_KEY`. Its `delivery: proxy` setting keeps the credential and any
resolver placeholder out of the sandbox. Pi sends the non-secret placeholder
declared by `models.json`; after admission and middleware processing succeed,
the OpenShell supervisor replaces that authorization header with the real,
endpoint-bound credential immediately before forwarding the request.

At the Pi prompt, submit both of these in the same session:

```text
Reply with exactly: DENY_THIS
```

```text
Reply with exactly: REDACT_THIS
```

The first submission is denied without starting a model request. The second
makes a request containing `[REDACTED]`.

To exercise tool-result admission without putting the marker in the user
message, ask Pi:

```text
Use bash to print the concatenation of DENY_ and THIS, then tell me the output.
```

The tool runs, but its result is replaced by Pi's protocol-safe blocked result
before it enters live context. Repeat with `REDACT_` and `THIS` to see the tool
result admitted as `[REDACTED]`.

Pi uses its standard session manager and JSONL session location, and exposes
the active path to tools as `PI_SESSION_FILE`. Admission runs before a user
message or tool result reaches that history. `launch` preserves the history;
`reset` and `cleanup` delete it with the sandbox.

## How it works

1. Pi exposes a provider-neutral `runCli()` entrypoint and a `RuntimeExtension`
   interface for mandatory `ContextAdmission` hooks. The TypeScript
   [openshell-pi.ts](runtime-extension/openshell-pi.ts) launcher calls that
   entrypoint with the OpenShell adapter from
   [openshell-context-admission.ts](runtime-extension/openshell-context-admission.ts).
   `prepare` type-checks both files against the packaged Pi API and compiles
   them to JavaScript for the sandbox. Pi otherwise starts normally, including
   standard project and user extension discovery.
2. Pi calls that boundary for each rendered user message and finalized tool
   result before it queues, appends, or persists the value.
3. The external adapter sends the exact context addition to OpenShell's
   sandbox-local bridge. Egress Gate applies `policy.yaml` and returns allow,
   deny, or a complete replacement. This append-time checkpoint returns no
   attestation or handle.
4. Immediately before every provider request, Pi passes the exact outbound
   context through admission. This includes normal turns, retries, compaction,
   branch summaries, and contexts restored from a prior session. The adapter
   applies per-entry replacements and obtains one fresh handle for the complete
   ordered user/tool context.
5. OpenShell keeps the signed whole-context attestation and gives Pi only the
   opaque handle, which the adapter keeps outside Pi messages. At egress,
   OpenShell strips the handle and supplies the attestation only to the
   configured Egress Gate stage. Egress Gate verifies the same ordered entries
   before and after request policy runs, before OpenShell injects the
   proxy-delivered model credential.

This division is intentional. The Pi fork contributes only reusable harness
primitives: mandatory admission of user messages and finalized tool results,
admission of the exact provider context, an outbound-header transformation, and
a standard-CLI entrypoint that accepts those hooks. OpenShell contributes the
sandbox-local bridge, signed attestations, attestation-to-request binding,
middleware enforcement, and post-policy credential delivery. The TypeScript
files under
`runtime-extension/` are the reusable integration layer that translates between
those generic Pi hooks and the OpenShell protocol; no OpenShell-specific code is
built into Pi.

The current OpenShell bridge is supervisor-owned but reachable by every process
inside the sandbox over loopback; it does not yet authenticate the calling
process. Whole-context attestation binding still prevents an unadmitted
provider request from passing the egress middleware. However, OpenShell cannot
prove that the designated harness invoked admission before changing its own
local memory or session files. A stronger runtime needs one additional
OpenShell primitive: a
process-scoped admission capability, or a supervisor-owned adapter channel that
only the designated harness can invoke.

## Current scope

The attestation adapter supports normal text turns, text tool results, queued
steering and follow-up messages, retries, automatic model continuations,
compaction, branch summaries, and restored sessions using the OpenAI Chat
Completions and Responses wire formats. Image inputs are outside this example's
current scope and fail closed.

Provider-context admission runs before Pi's transport-specific history
rewrites. Switching transports with existing tool history or sending orphaned
tool calls may therefore fail closed. Start a fresh session when switching
transports, and complete each tool-call/result sequence before sending.

## Cleanup

Exit Pi, but leave the OpenShell gateway running while cleanup deletes the
sandbox and provider:

```shell
./demo.sh cleanup
```

Then stop the OpenShell gateway and Egress Gate with `Ctrl-C`. For another
session in the same prepared sandbox, use `./demo.sh launch` instead of cleanup.
