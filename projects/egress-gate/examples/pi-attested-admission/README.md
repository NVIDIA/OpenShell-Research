# Managed Pi deny-or-redact example

This example runs the forked Pi CLI inside OpenShell and sends its rendered
user submissions through Egress Gate. It makes real OpenAI API calls and may
incur provider charges.

- `DENY_THIS` is rejected before Pi writes it to session history or starts a
  model turn.
- `REDACT_THIS` becomes `[REDACTED]` before Pi writes or sends it.

## Before you start

Use these matching fork branches:

- [Pi `johnny/before-user-message-commit`](https://github.com/johnnygreco/pi/tree/johnny/before-user-message-commit)
- [OpenShell `openshell/pi-egress-admission`](https://github.com/johnnygreco/OpenShell/tree/openshell/pi-egress-admission)
- [OpenShell Research integration branch](https://github.com/NVIDIA/OpenShell-Research/tree/johnny/pi-attested-admission)

Install each repository's development prerequisites and export:

```shell
export OPENAI_API_KEY=your-key
export EGRESS_GATE_HOST_IP=192.168.1.20
```

`EGRESS_GATE_HOST_IP` must be a non-loopback IPv4 address reachable by the
gateway and sandbox supervisors. `hostname -I` usually shows the available
addresses; choose the address for the host network shared with OpenShell.

The helper expects sibling checkouts named `pi`, `OpenShell`, and
`OpenShell-Research`. For another layout, set `PI_REPO` and `OPENSHELL_REPO` to
absolute paths.

If you do not already have the fork checkouts, clone them beside this repository:

```shell
git clone --branch johnny/before-user-message-commit https://github.com/johnnygreco/pi.git ../pi
git clone --branch openshell/pi-egress-admission https://github.com/johnnygreco/OpenShell.git ../OpenShell
```

From the `OpenShell-Research` checkout, change to the example directory. Run
all remaining commands there:

```shell
cd projects/egress-gate/examples/pi-attested-admission
```

You can inspect every command before running anything:

```shell
./demo.sh --print all
```

Update both fork checkouts to the latest commits on those branches:

```shell
./demo.sh sync
```

`sync` uses fast-forward-only pulls and stops instead of merging divergent local
work.

## Try it

Build the Pi fork and register Egress Gate with OpenShell:

```shell
./demo.sh prepare
```

Keep Egress Gate running in one terminal:

```shell title="Terminal 1: Egress Gate"
./demo.sh serve
```

Start the matching OpenShell gateway in a second terminal:

```shell title="Terminal 2: OpenShell gateway"
./demo.sh gateway
```

After the gateway reports that it is ready, launch the real Pi CLI from a
third terminal:

```shell title="Terminal 3: managed Pi"
./demo.sh launch
```

At the Pi prompt, submit both of these in the same session:

```text
Reply with exactly: DENY_THIS
```

```text
Reply with exactly: REDACT_THIS
```

The first submission is denied without starting a model turn. The second makes
a real model call using `[REDACTED]`. Exit Pi, then inspect its persisted
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
4. Pi appends only admitted or replacement text to session history.
5. OpenShell checks the receipt before the model request leaves the sandbox and
   injects `OPENAI_API_KEY`; the key is never copied into the sandbox.

## Inspect individual commands

The helper never requires you to trust hidden orchestration. Add `--print` to
any action to show its exact commands without executing them:

```shell
./demo.sh --print prepare
./demo.sh --print launch
```

The actions are deliberately small: `sync` updates the two fork branches;
`prepare` builds and packages the Pi fork; `serve` runs Egress Gate; `gateway`
runs the matching OpenShell fork; and `launch` creates the credential provider
and sandbox.

## Current scope

This initial integration supports idle, text-only, direct OpenAI Chat
Completions submissions. Images, queued input, retries, compaction, and
automatic continuations after tool calls are unsupported and fail closed. The
next comprehensive boundary is one receipt per provider request; it does not
require one Pi hook per message role.

## Cleanup

Exit Pi, but leave the OpenShell gateway running while cleanup deletes the
sandbox and provider:

```shell
./demo.sh cleanup
```

Then stop the OpenShell gateway and Egress Gate with `Ctrl-C`. To run the
example again, start from `./demo.sh sync`.
