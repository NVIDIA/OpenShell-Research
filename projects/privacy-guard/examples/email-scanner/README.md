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

Run the example commands from this directory:

```bash
cd projects/privacy-guard/examples/email-scanner
```

## 1. Edit the example config

Find the host address that Docker can reach:

```bash
ipconfig getifaddr en0
```

Replace `REPLACE_WITH_HOST_IP` in `gateway.toml` with that address. If `en0`
does not return an address, find the active interface with:

```bash
route get default | grep interface
```

Only the checked-out `gateway.toml` is edited. Do not copy it into
`~/.config/openshell`.

## 2. Start Privacy Guard

This development server uses unauthenticated plaintext gRPC and receives request
bodies that may contain sensitive content. Restrict it to a trusted network and
firewall the port. When possible, bind `--listen` to the specific host interface
that Docker must reach instead of every interface.

In terminal 1:

```bash
uv run --project ../.. python middleware_server.py \
  --listen 0.0.0.0:50051
```

Leave it running.

## 3. Run the installed gateway with the example config

In terminal 2:

```bash
brew services stop openshell

OPENSHELL_LOCAL_TLS_DIR="$HOME/.local/state/openshell/homebrew/tls" \
openshell-gateway --config "$PWD/gateway.toml"
```

The first command stops the background service so the foreground gateway can use
the standard port. The second command reuses the credentials and state created
by the recommended macOS installation, but loads `gateway.toml` from this
directory. It should stay in the foreground; `Server listening` means it is
ready.

Middleware registration is static. After editing `gateway.toml`, stop this
foreground process with `Ctrl-C` and run the second command again.

## 4. Create the sandbox and run Claude

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
openshell policy set privacy-guard-lab --policy "$PWD/policy.yaml" --wait
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
