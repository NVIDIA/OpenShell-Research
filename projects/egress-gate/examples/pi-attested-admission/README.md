# Managed Pi attested-admission example

This example runs the forked Pi CLI inside OpenShell and sends admitted user
submissions to a model endpoint you choose. The endpoint may be a hosted
provider, an internal gateway, or a local server. It must accept the OpenAI Chat
Completions request shape used by the current attestation adapter; it does not
need to be OpenAI.

The example demonstrates two outcomes:

- `DENY_THIS` is rejected before Pi records it or starts a model request.
- `REDACT_THIS` becomes `[REDACTED]` before Pi records or sends it.

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

If the model endpoint does not require authentication, set
`PI_MODEL_API_KEY=unused`. Source `.env` again in each new terminal that runs
`demo.sh`.

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

Prepare the forks, build Pi, generate the endpoint-specific runtime
configuration, and generate the Egress Gate registration used by Terminal 2:

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

```shell title="Terminal 3: managed Pi"
./demo.sh launch
```

Each launch replaces the example's `pi-egress-demo` sandbox, provider, and
custom provider profile so the current Pi runtime, extension, policy, endpoint,
and OpenShell supervisor are used together.

The example registers an endpoint-specific provider profile using the host-side
`PI_MODEL_API_KEY`. Inside the sandbox it uses the distinct
`MODEL_PROVIDER_API_KEY` name so Pi's own `PI_*` diagnostics do not capture the
credential. The extension redacts accidental appearances in tool output, and
OpenShell blocks any credential-bearing request body from leaving the sandbox.

At the Pi prompt, submit both of these in the same session:

```text
Reply with exactly: DENY_THIS
```

```text
Reply with exactly: REDACT_THIS
```

The first submission is denied without starting a model request. The second
makes a request containing `[REDACTED]`. Exit Pi, then inspect its persisted
session:

```shell
./demo.sh verify
```

The output must contain `[REDACTED]` and must not contain `DENY_THIS` or
`REDACT_THIS`. The command exits with an error if either check fails.

## How it works

1. Pi renders the user submission and calls its general-purpose
   `before_user_message_append` extension hook.
2. The example extension sends that text to OpenShell's sandbox-local admission
   bridge.
3. Egress Gate applies `policy.yaml`: it either denies the submission or
   returns replacement text plus a short-lived receipt.
4. Pi records only admitted or replacement text.
5. Before each model request in that turn, including automatic requests after
   tool calls, the extension obtains a fresh receipt for the active admitted
   text.
6. As each request leaves the sandbox, Egress Gate verifies that its final user
   text matches the receipt and OpenShell resolves the credential.

## Current scope

The attestation adapter supports normal text turns, including tools, queued
steering and follow-up messages, and the automatic model continuations they
produce, using the OpenAI Chat Completions wire format. Providers with a
different native protocol and image inputs are not covered by this example and
fail closed.

## Cleanup

Exit Pi, but leave the OpenShell gateway running while cleanup deletes the
sandbox and provider:

```shell
./demo.sh cleanup
```

Then stop the OpenShell gateway and Egress Gate with `Ctrl-C`. To run the
example again, start from `./demo.sh prepare`.
