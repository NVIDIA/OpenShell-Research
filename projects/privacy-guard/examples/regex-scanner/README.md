# Built-in regex scanner walkthrough

This self-contained example runs Privacy Guard's built-in `RegexScanner`,
registers it with an OpenShell gateway, and uses a sandbox policy to redact an
email address and customer ID before Claude Code sends a request to Anthropic.
It is intended to be worked through by hand.

The example temporarily runs the installed OpenShell gateway with the config in
this directory. It does not create or modify `~/.config/openshell/gateway.toml`
or create a project-local state directory. It registers the standard local
gateway endpoint with the OpenShell CLI.

## Prerequisites

- macOS with Docker Desktop running
- Python 3 and `uv`
- OpenShell installed with its recommended installer:

  ```bash
  curl -LsSf https://raw.githubusercontent.com/NVIDIA/OpenShell/main/install.sh | sh
  ```

Run all commands in this walkthrough from the example directory:

```bash
cd projects/privacy-guard/examples/regex-scanner
```

`uv` automatically discovers the Privacy Guard project in the parent
directories, so the commands do not need a `--project` option.

## 1. Review the scanner configuration

`regex-scanner.yaml` configures two entities:

- `email` matches common email-shaped text.
- `customer-id` matches identifiers such as `CUST-12345678`.

The patterns are intentionally small and understandable. Treat them as a
starting point, not comprehensive production detection.

## 2. Generate the local gateway config

Generate a local config containing a host address that both the gateway and
sandboxes can reach:

```bash
uv run python generate_gateway_config.py
```

This writes the ignored `gateway.local.toml`. The checked-in `gateway.toml`
remains a reusable template.

## 3. Start Privacy Guard

This development server uses unauthenticated plaintext gRPC and receives request
bodies that may contain sensitive content. Restrict access to port 50051 with a
host firewall because sandboxes must be able to reach it.

In terminal 1:

```bash
uv run privacy-guard regex \
  --config regex-scanner.yaml \
  --listen 0.0.0.0:50051
```

Leave it running. Privacy Guard loads and compiles the scanner configuration
before it binds the port.

To investigate request latency without logging request content, restart Privacy
Guard with `--debug` before the `regex` command:

```bash
uv run privacy-guard --debug regex \
  --config regex-scanner.yaml \
  --listen 0.0.0.0:50051
```

Debug output reports body size, text-block count, scanned character count, and
normalize, scan, and reconstruction timings. It never reports body content,
matched values, JSON paths, headers, or scanner configuration.

To compare the complete body received by Privacy Guard with the body it returns
to OpenShell, use the explicit content-logging mode instead:

```bash
uv run privacy-guard --debug-log-content regex \
  --config regex-scanner.yaml \
  --listen 0.0.0.0:50051
```

Look for `stage=received` and `stage=forwarded` entries with the same
`request_id`. This mode also enables phase timings. It logs secrets by design,
so use it only for local debugging and disable it after capturing the failing
request.

## 4. Run the installed gateway with the example config

In terminal 2:

```bash
brew services stop openshell

OPENSHELL_LOCAL_TLS_DIR="$HOME/.local/state/openshell/homebrew/tls" \
openshell-gateway --config "$PWD/gateway.local.toml"
```

The first command stops the background service so the foreground gateway can use
the standard port. The second command reuses the credentials and state created
by the recommended macOS installation, but loads this example's generated
`gateway.local.toml`. Keep it in the foreground; `Server listening` means it is
ready.

Middleware registration is static. After regenerating `gateway.local.toml`, stop
this foreground process with `Ctrl-C` and run the second command again.

## 5. Create the sandbox and run Claude

In terminal 3:

```bash
openshell gateway add \
  https://127.0.0.1:17670 \
  --local \
  --name openshell

openshell status

openshell sandbox create \
  --name privacy-guard-regex-lab \
  --from base \
  --no-auto-providers \
  --policy "$PWD/policy.yaml" \
  -- env CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 claude
```

`gateway add` saves the endpoint as the CLI's active gateway and refreshes its
package-managed mTLS credentials. The registration continues to target the
normal local gateway after this walkthrough.

Disabling nonessential Claude Code traffic keeps telemetry and error-reporting
batches out of this focused redaction exercise. Privacy Guard still processes
the model requests sent to `api.anthropic.com`.

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

To change enforcement, edit `policy.yaml` and set `on_finding.action` to
`observe`, `block`, or `redact`, then apply it without recreating the sandbox:

```bash
openshell policy set privacy-guard-regex-lab \
  --policy "$PWD/policy.yaml" \
  --wait
```

- `redact` sends `[email]` and `[customer-id]` to Claude.
- `observe` records findings but sends the original values.
- `block` denies a request containing either configured entity.

To change detection, edit `regex-scanner.yaml`, stop Privacy Guard with `Ctrl-C`,
and run the terminal 1 command again. Scanner configuration is loaded only at
startup.

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

Stop Privacy Guard with `Ctrl-C` in terminal 1. The example-specific gateway
configuration was never installed as the default configuration.

This example uses Claude Code because subscription prompts are sent in
inspectable HTTP request bodies. ChatGPT-subscription Codex currently sends
prompts in WebSocket frames, which this HTTP middleware cannot inspect.
