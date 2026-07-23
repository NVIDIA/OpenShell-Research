# Email scanner example

This self-contained example supplies a deterministic custom email scanner, its
Privacy Guard server entry point, and the OpenShell gateway and sandbox policy
needed to try redaction with Claude Code. It temporarily runs the installed
OpenShell gateway with the config from this directory. It does not create or modify
`~/.config/openshell/gateway.toml`, and it does not create a project-local state
directory.

The `EmailScanner` class in `middleware_server.py` demonstrates how to implement
the scanner extension contract directly. It detects email-shaped text in Claude
Code request bodies. The policy replaces matches with `[email]` before Anthropic
receives the request. The implementation is intentionally small and is not
intended as comprehensive production PII detection.

## Prerequisites

- macOS with Docker Desktop running
- Python 3 and `uv`
- OpenShell installed with its recommended installer:

  ```bash
  curl -LsSf https://raw.githubusercontent.com/NVIDIA/OpenShell/main/install.sh | sh
  ```

Run all commands in this walkthrough from the example directory:

```bash
cd projects/privacy-guard/examples/email-scanner
```

`uv` automatically discovers the Privacy Guard project in the parent
directories, so the commands do not need a `--project` option.

## 1. Start Privacy Guard

This development server uses unauthenticated plaintext gRPC and receives request
bodies that may contain sensitive content. It listens only on loopback because
the gateway and Privacy Guard both run on the host.

In terminal 1:

```bash
uv run python middleware_server.py \
  --listen 127.0.0.1:50051
```

Leave it running.

## 2. Run the installed gateway with the example config

In terminal 2:

```bash
brew services stop openshell

OPENSHELL_LOCAL_TLS_DIR="$HOME/.local/state/openshell/homebrew/tls" \
openshell-gateway --config "$PWD/gateway.toml"
```

The first command stops the background service so the foreground gateway can use
the standard port. The second command reuses the credentials and state created
by the recommended macOS installation, but loads this example's `gateway.toml`.
It should stay in the foreground; `Server listening` means it is ready.

The gateway connects to Privacy Guard over host loopback. The
`host.openshell.internal` hostname is only needed when a process inside a
sandbox connects back to a service on the host.

Middleware registration is static. After editing `gateway.toml`, stop this
foreground process with `Ctrl-C` and run the second command again.

## 3. Create the sandbox and run Claude

In terminal 3:

```bash
openshell status

openshell sandbox create \
  --name privacy-guard-lab \
  --from base \
  --no-auto-providers \
  --policy "$PWD/policy.yaml" \
  -- claude
```

Choose Claude Code's subscription-account login and complete authentication.
Then enter:

```text
Tell me something that rhymes with my email wendy@gmail.com
```

Privacy Guard should replace the address with `[email]` before Claude sees it.
Model output is nondeterministic, so use OpenShell's logs as the authoritative
check:

```bash
openshell logs privacy-guard-lab --tail
```

Look for the `api.anthropic.com/v1/messages` request with `transformed:true` and
an email finding.

## Change the behavior

Edit `policy.yaml` and change `on_finding.action` to `observe`, `block`, or
`redact`, then apply it without recreating the sandbox:

```bash
openshell policy set privacy-guard-lab \
  --policy "$PWD/policy.yaml" \
  --wait
```

- `redact` sends `[email]` to Claude.
- `observe` records the finding but sends the original email.
- `block` denies the request.

To reconnect later:

```bash
openshell sandbox connect privacy-guard-lab
```

Then run `claude` inside the sandbox.

## Cleanup

Exit Claude and the sandbox, then delete it:

```bash
openshell sandbox delete privacy-guard-lab
```

Stop the foreground gateway with `Ctrl-C` in terminal 2, then restore the normal
background gateway:

```bash
brew services start openshell
```

Stop Privacy Guard with `Ctrl-C` in terminal 1. No default OpenShell config was
changed.

This example uses Claude Code because subscription prompts are sent in inspectable
HTTP request bodies. ChatGPT-subscription Codex currently sends prompts in
WebSocket frames, which this HTTP middleware cannot inspect.
