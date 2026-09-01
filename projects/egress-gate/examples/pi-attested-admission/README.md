# Pi attested-admission example

This example runs a normal interactive Pi TUI inside OpenShell and sends
admitted conversation context to a model endpoint you choose. The endpoint may be a hosted
provider, an internal gateway, or a local server. It must accept the OpenAI Chat
Completions request shape used by the current attestation adapter; it does not
need to be OpenAI.

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
`PI_MODEL_API_KEY=unused`. Source `.env` again in each new terminal that runs
`demo.sh`.

The generated Pi model enables Pi's normal reasoning controls and sends the
OpenAI-compatible `reasoning_effort` request field to the configured endpoint.

`EGRESS_GATE_HOST_IP` is the address OpenShell uses to reach Egress Gate on this
machine. It must be a reachable, non-loopback IPv4 address; do not use
`127.0.0.1`. `PI_MODEL_BASE_URL` is separate: it is the model endpoint Pi will
call. A model server running on this machine must likewise use a hostname or
address reachable from the sandbox rather than `localhost`.

`demo.sh prepare` derives the endpoint policy and Pi model configuration from
these values. You do not need to edit `policy.yaml`. If required values are
missing or still contain placeholders, the script prints the configuration
steps and stops before performing any work.

Preview the complete workflow before running anything:

```shell
./demo.sh --print all
```

The walkthrough lists the terminal sequence and configuration visible to the
current shell. To inspect the exact commands for one action, use its name—for
example, `./demo.sh --print prepare` or `./demo.sh --print launch`.

## Run the example

Prepare the forks, build and package the locally modified Pi agent core and
coding agent, generate the endpoint-specific runtime configuration, and
generate the Egress Gate registration used by Terminal 2:

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

After the gateway reports that it is ready, launch Pi from a third terminal:

```shell title="Terminal 3: Pi"
./demo.sh launch
```

This starts the standard Pi CLI, with OpenShell admission inserted immediately
before each provider request. Each launch replaces the example's
`pi-egress-demo` sandbox, provider, and custom provider profile so the current
Pi runtime, admission adapter, policy, endpoint, and OpenShell supervisor are
used together.

The example registers an endpoint-specific provider profile using the host-side
`PI_MODEL_API_KEY`. The real credential remains in OpenShell. Pi receives only
an opaque, endpoint-bound resolver placeholder. The launcher removes that
placeholder before starting Pi and passes it once over a private file
descriptor. The thin launcher reads and closes the descriptor before the TUI or
tools start, supplies the resolver to Pi's non-persistent runtime credential
store, installs mandatory admission, and delegates the rest of startup to Pi's
standard CLI. OpenShell resolves the placeholder in the authorization header
for the configured model endpoint.

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
message or tool result reaches that history. Each `launch` replaces the
disposable demo sandbox, so copy out anything you want to retain before ending
the run.

## How it works

1. `managed-pi.ts` calls Pi's standard `main()` with two runtime hooks: one
   installs the opaque credential resolver and one creates the mandatory SDK
   `ContextAdmission` boundary for each normal Pi session. The launch command
   uses Pi's standard `--no-extensions` option, so project or user extensions
   cannot replace this boundary.
2. Pi calls that boundary for each rendered user message and finalized tool
   result before it queues, appends, or persists the value.
3. The adapter sends the exact context addition to OpenShell's sandbox-local
   bridge. Egress Gate applies `policy.yaml` and returns allow, deny, or a
   complete replacement.
4. OpenShell keeps the signed attestation and gives Pi only an opaque handle.
   The adapter keeps handles in its private closure, outside Pi messages.
5. Immediately before every provider request, Pi passes the exact outbound
   context through admission. This includes normal turns, retries, compaction,
   branch summaries, and contexts restored from a prior session. The adapter
   applies any replacement and obtains a fresh handle for the newest user
   message or tool result in that exact context.
6. OpenShell strips the handle, resolves the supervisor-held attestation, and
   supplies it only to the configured Egress Gate middleware stage. Egress Gate
   verifies the latest context addition and scans the complete provider request
   before OpenShell resolves the model credential.

## Current scope

The attestation adapter supports normal text turns, text tool results, queued
steering and follow-up messages, retries, automatic model continuations,
compaction, branch summaries, and restored sessions using the OpenAI Chat
Completions wire format. Providers with a different native protocol and image
inputs are not covered by this example and fail closed.

## Cleanup

Exit Pi, but leave the OpenShell gateway running while cleanup deletes the
sandbox and provider:

```shell
./demo.sh cleanup
```

Then stop the OpenShell gateway and Egress Gate with `Ctrl-C`. To run the
example again, start from `./demo.sh prepare`.
