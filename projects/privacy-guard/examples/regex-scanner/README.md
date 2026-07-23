# Built-in regex scanner walkthrough

This self-contained example runs Privacy Guard's built-in `RegexScanner`,
registers it with an OpenShell gateway, and uses a sandbox policy to redact an
email address and customer ID before Claude Code sends a request to Anthropic.
It is intended to be worked through by hand.

The example temporarily runs the installed OpenShell gateway with the config in
this directory. It does not create or modify
`~/.config/openshell/gateway.toml`, and it does not create a project-local state
directory.

## Prerequisites

- macOS with Docker Desktop running
- Python 3 and `uv`
- OpenShell installed with its recommended installer:

  ```bash
  curl -LsSf https://raw.githubusercontent.com/NVIDIA/OpenShell/main/install.sh | sh
  ```

Run all commands in this walkthrough from the Privacy Guard project root:

```bash
cd projects/privacy-guard
```

In particular, every `uv run` command below assumes this working directory.

## 1. Review the scanner configuration

`regex-scanner.yaml` configures two entities:

- `email` matches common email-shaped text.
- `customer-id` matches identifiers such as `CUST-12345678`.

The patterns are intentionally small and understandable. Treat them as a
starting point, not comprehensive production detection.

## 2. Generate the local gateway config

Generate `gateway.local.toml` with the address of the host interface Docker can
reach:

```bash
HOST_INTERFACE="$(route get default | awk '/interface:/{print $2}')"
HOST_IP="$(ipconfig getifaddr "$HOST_INTERFACE")"
test -n "$HOST_IP"
sed "s/REPLACE_WITH_HOST_IP/$HOST_IP/" \
  examples/regex-scanner/gateway.toml \
  > examples/regex-scanner/gateway.local.toml
```

The checked-in `gateway.toml` remains an example-specific template.
`gateway.local.toml` is ignored by Git. Do not copy either file into
`~/.config/openshell`.

## 3. Start Privacy Guard

This development server uses unauthenticated plaintext gRPC and receives request
bodies that may contain sensitive content. Restrict it to a trusted network and
firewall the port. When possible, replace `0.0.0.0` with the specific host
interface that Docker must reach.

In terminal 1:

```bash
uv run privacy-guard regex \
  --config examples/regex-scanner/regex-scanner.yaml \
  --listen 0.0.0.0:50051
```

Leave it running. Privacy Guard loads and compiles the scanner configuration
before it binds the port.

## 4. Run the installed gateway with the example config

In terminal 2:

```bash
brew services stop openshell

OPENSHELL_LOCAL_TLS_DIR="$HOME/.local/state/openshell/homebrew/tls" \
openshell-gateway --config "$PWD/examples/regex-scanner/gateway.local.toml"
```

The first command stops the background service so the foreground gateway can use
the standard port. The second command reuses the credentials and state created
by the recommended macOS installation, but loads the generated local config.
Keep it in the foreground; `Server listening` means it is ready.

Middleware registration is static. After regenerating `gateway.local.toml`, stop
this foreground process with `Ctrl-C` and run the second command again.

## 5. Create the sandbox and run Claude

In terminal 3:

```bash
openshell status

openshell sandbox create \
  --name privacy-guard-regex-lab \
  --from base \
  --no-auto-providers \
  --policy "$PWD/examples/regex-scanner/policy.yaml" \
  -- claude
```

Choose Claude Code's subscription-account login and complete authentication.
Then enter:

```text
Tell me something that rhymes with my email wendy@gmail.com and remember that my customer ID is CUST-12345678.
```

Privacy Guard should replace the values with `[email]` and `[customer-id]`
before Claude sees them. Model output is nondeterministic, so use OpenShell's
logs as the authoritative check:

```bash
openshell logs privacy-guard-regex-lab --tail
```

Look for the `api.anthropic.com/v1/messages` request with `transformed:true` and
findings for both configured entities.

## Change the behavior

To change enforcement, edit `examples/regex-scanner/policy.yaml` and set
`on_finding.action` to `observe`, `block`, or `redact`, then apply it without
recreating the sandbox:

```bash
openshell policy set privacy-guard-regex-lab \
  --policy "$PWD/examples/regex-scanner/policy.yaml" \
  --wait
```

- `redact` sends `[email]` and `[customer-id]` to Claude.
- `observe` records findings but sends the original values.
- `block` denies a request containing either configured entity.

To change detection, edit `examples/regex-scanner/regex-scanner.yaml`, stop
Privacy Guard with `Ctrl-C`, and run the terminal 1 command again. Scanner
configuration is loaded only at startup.

To reconnect later:

```bash
openshell sandbox connect privacy-guard-regex-lab
```

Then run `claude` inside the sandbox.

## Cleanup

Exit Claude and the sandbox, then delete it:

```bash
openshell sandbox delete privacy-guard-regex-lab
```

Stop the foreground gateway with `Ctrl-C` in terminal 2, then restore the normal
background gateway:

```bash
brew services start openshell
```

Stop Privacy Guard with `Ctrl-C` in terminal 1. No default OpenShell config was
changed.

This example uses Claude Code because subscription prompts are sent in
inspectable HTTP request bodies. ChatGPT-subscription Codex currently sends
prompts in WebSocket frames, which this HTTP middleware cannot inspect.
