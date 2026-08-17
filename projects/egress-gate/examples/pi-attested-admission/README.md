# Managed Pi deny-or-redact example

This directory contains a real OpenShell configuration for running the Pi
admission extension with Egress Gate. It does not contain a simulated Pi
session or provider.

The policy demonstrates two outcomes for rendered Pi prompts:

- `DENY_THIS` denies the submission before Pi appends it to session history or
  starts a provider request.
- `REDACT_THIS` becomes `[REDACTED]` before Pi appends the submission. Pi sends
  that same replacement in the provider request.

This example makes real OpenAI API calls and may incur provider charges.

## Prerequisites

Use these matching fork branches:

- [Pi user-message append hook PR](https://github.com/johnnygreco/pi/pull/1)
- [OpenShell integration branch](https://github.com/johnnygreco/OpenShell/tree/openshell/pi-egress-admission)
- [OpenShell Research integration branch](https://github.com/NVIDIA/OpenShell-Research/tree/johnny/pi-attested-admission)

Install the development prerequisites documented by each repository. The host
must have an `OPENAI_API_KEY`, and the host, gateway, and sandbox supervisor
must be able to reach the Egress Gate service.

The instructions below use these checkout placeholders:

```text
/path/to/pi
/path/to/OpenShell
/path/to/OpenShell-Research
```

Replace them with absolute paths on your machine.

## 1. Build the Pi fork

Build the coding-agent package from the Pi fork, pack it, and install it into a
standalone directory that can be uploaded to a sandbox:

```shell
cd /path/to/pi
npm install --ignore-scripts
npm run build
mkdir -p /tmp/pi-egress-pack /tmp/pi-egress-runtime
npm pack --workspace @earendil-works/pi-coding-agent \
  --pack-destination /tmp/pi-egress-pack
```

The last command prints the tarball name. Pass that exact file to:

```shell
npm install --prefix /tmp/pi-egress-runtime --ignore-scripts \
  /tmp/pi-egress-pack/earendil-works-pi-coding-agent-VERSION.tgz
```

Replace `VERSION` with the version in the printed filename. The built CLI entry
point is then
`/tmp/pi-egress-runtime/node_modules/@earendil-works/pi-coding-agent/dist/cli.js`.

## 2. Register and start Egress Gate

Stop any OpenShell gateway that uses the target gateway configuration. A
running gateway does not reload middleware registrations.

From the Egress Gate project, add the operator middleware registration. Replace
`YOUR_HOST_IPV4` with a non-loopback IPv4 address reachable by the gateway and
sandbox supervisors:

```shell
cd /path/to/OpenShell-Research/projects/egress-gate
uv run egress-gate add-gateway-registration \
  --host-ip YOUR_HOST_IPV4 \
  --name pi-egress \
  --port 50051
```

In the same directory, start Egress Gate with Pi receipt enforcement enabled:

```shell
uv run egress-gate --debug serve \
  --listen 0.0.0.0:50051 \
  --timeout 4s \
  --require-pi-receipt
```

Keep this terminal open. The service exposes both the rendered-prompt admission
binding and the HTTP egress binding used by this example.

## 3. Start the OpenShell fork

In another terminal, start the gateway from the matching OpenShell fork. It
loads the `pi-egress` registration added above:

```shell
cd /path/to/OpenShell
mise trust
mise run gateway
```

Leave the gateway running. Use the repository's `scripts/bin/openshell` wrapper
for the remaining OpenShell commands so the CLI and gateway come from the same
fork.

## 4. Create an OpenAI provider

In a third terminal, create a provider whose credential is injected only when
the admitted request reaches `api.openai.com`:

```shell
cd /path/to/OpenShell
/path/to/OpenShell/scripts/bin/openshell provider create \
  --name pi-openai \
  --type openai \
  --credential OPENAI_API_KEY
```

The bare credential name reads `OPENAI_API_KEY` from the host environment. It
does not place the real key in the sandbox environment.

## 5. Create the managed Pi sandbox

Run the following command from this example directory:

```shell
cd /path/to/OpenShell-Research/projects/egress-gate/examples/pi-attested-admission
/path/to/OpenShell/scripts/bin/openshell sandbox create \
  --name pi-egress-demo \
  --from base \
  --provider pi-openai \
  --policy policy.yaml \
  --upload /tmp/pi-egress-runtime:/sandbox/pi-runtime \
  --upload ./openshell-input-admission.ts:/sandbox/openshell-input-admission.ts \
  --upload ./models.json:/sandbox/pi-agent/models.json \
  -- env PI_CODING_AGENT_DIR=/sandbox/pi-agent \
  node /sandbox/pi-runtime/node_modules/@earendil-works/pi-coding-agent/dist/cli.js \
  --provider openai-chat-completions \
  --model gpt-4o-mini \
  --extension /sandbox/openshell-input-admission.ts \
  --session-dir /sandbox/pi-sessions
```

OpenShell recognizes the configured Pi admission binding, starts its
loopback-only bridge, and sets `OPENSHELL_PI_CONVERSATION_URL` for the Pi
process. The extension calls that bridge from `before_user_message_append` and
attaches the returned receipt to the first provider request. Pi itself contains
no OpenShell-specific startup behavior.

[`models.json`](models.json) pins this run to OpenAI Chat Completions. The
initial integration does not support the Responses API.

## 6. Verify denial

At the Pi prompt, submit:

```text
Reply with exactly: DENY_THIS
```

Pi reports that OpenShell denied the prompt and does not start a model turn.
Run `/session` before exiting Pi to see the active session file. After exiting,
inspect all example session files:

```shell
/path/to/OpenShell/scripts/bin/openshell sandbox exec -n pi-egress-demo -- \
  grep -R -n DENY_THIS /sandbox/pi-sessions
```

The command must produce no matches. The Egress Gate terminal has no
corresponding HTTP provider-request evaluation.

## 7. Verify replacement

Reconnect to the same sandbox and start Pi with the same extension and session
directory:

```shell
/path/to/OpenShell/scripts/bin/openshell sandbox exec -n pi-egress-demo --tty -- \
  env PI_CODING_AGENT_DIR=/sandbox/pi-agent \
  node /sandbox/pi-runtime/node_modules/@earendil-works/pi-coding-agent/dist/cli.js \
  --provider openai-chat-completions \
  --model gpt-4o-mini \
  --extension /sandbox/openshell-input-admission.ts \
  --session-dir /sandbox/pi-sessions
```

Submit:

```text
Reply with exactly: REDACT_THIS
```

The request makes a real model call. After exiting Pi, inspect the persisted
session:

```shell
/path/to/OpenShell/scripts/bin/openshell sandbox exec -n pi-egress-demo -- \
  grep -R -n -E 'REDACT_THIS|\[REDACTED\]' /sandbox/pi-sessions
```

The session must contain `[REDACTED]` and must not contain `REDACT_THIS`. The
Egress Gate terminal records an allowed provider-request evaluation. A
successful request also proves that its rendered prompt matched the admitted
replacement: Egress Gate rejects a receipt when the provider request contains a
different final user prompt. The network middleware consumes the receipt, then
removes the internal receipt header before forwarding upstream.

## Configuration correspondence

[`egress-gate-config.yaml`](egress-gate-config.yaml) is the standalone Egress
Gate configuration. [`policy.yaml`](policy.yaml) embeds that exact configuration
under `network_middlewares.pi_egress_gate.config`, attaches the registered
`pi-egress` service, selects exactly `api.openai.com`, and fails closed if the
middleware is unavailable.

OpenShell uses the same middleware configuration for rendered-prompt admission
and provider HTTP egress. This is what lets Egress Gate issue a receipt before
Pi persists the candidate and verify it again at the network boundary.

## Current scope

This initial integration supports idle, text-only, direct OpenAI Chat
Completions submissions. Images, queued input, retries, compaction, and
automatic continuations after tool calls are unsupported and fail closed. The
next comprehensive boundary is one receipt per provider request; it does not
require one Pi hook per message role.

## Cleanup

Delete the sandbox and provider:

```shell
cd /path/to/OpenShell
/path/to/OpenShell/scripts/bin/openshell sandbox delete pi-egress-demo
/path/to/OpenShell/scripts/bin/openshell provider delete pi-openai
```

Stop the gateway before removing its static middleware registration, then
restart it:

```shell
cd /path/to/OpenShell-Research/projects/egress-gate
uv run egress-gate remove-gateway-registration --name pi-egress
```
