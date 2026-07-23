# Email scanner example

This self-contained example supplies a deterministic custom email scanner, its
Privacy Guard server entry point, and the OpenShell gateway and sandbox policy
needed to try redaction with Claude Code. It temporarily runs the installed
OpenShell gateway with the config from this directory. It does not create or
modify `~/.config/openshell/gateway.toml` or create a project-local state
directory. It registers the standard local gateway endpoint with the OpenShell
CLI.

The `EmailScanner` class in `middleware_server.py` demonstrates how to implement
the scanner extension contract directly. It detects email-shaped text in Claude
Code request bodies. The policy replaces matches with `[email]` before the
remote model receives the request. The implementation is intentionally small
and is not intended as comprehensive production PII detection.

## Before you start

- macOS with Docker Desktop running
- Python 3 and `uv`
- OpenShell installed with its recommended installer:

  ```bash
  curl -LsSf https://raw.githubusercontent.com/NVIDIA/OpenShell/main/install.sh | sh
  ```

The foreground gateway commands below target the Homebrew installation on
macOS. The scanner, gateway config, and sandbox policy also work on Linux, but
use the service and TLS paths from that OpenShell installation.

Run all commands in this walkthrough from the example directory:

```bash
cd projects/privacy-guard/examples/email-scanner
```

`uv` automatically discovers the Privacy Guard project in the parent
directories, so the commands do not need a `--project` option.

Use terminal 1 for Privacy Guard, terminal 2 for the foreground gateway,
terminal 3 for Claude Code, and another terminal for finite log checks.

## 1. Generate the local gateway config

Enter the IPv4 address of the host's physical Ethernet or Wi-Fi interface after
`YOUR_HOST_IP=`, then run both lines:

```bash
YOUR_HOST_IP=
sed "s/REPLACE_WITH_HOST_IP/$YOUR_HOST_IP/" gateway.toml > gateway.local.toml
grep grpc_endpoint gateway.local.toml
```

Do not use `127.0.0.1`, a VPN address, or `host.openshell.internal`. The address
must be reachable from both the host gateway and sandbox supervisor.
The final command lets you verify the generated endpoint before starting
anything.

## 2. Start Privacy Guard

This development server uses unauthenticated plaintext gRPC and receives request
bodies that may contain sensitive content. Restrict access to port 50051 with a
host firewall because sandboxes must be able to reach it.

In terminal 1:

```bash
uv run python middleware_server.py \
  --listen 0.0.0.0:50051
```

Leave it running.

## 3. Run the installed gateway with the example config

In terminal 2:

```bash
brew services stop openshell

OPENSHELL_LOCAL_TLS_DIR="$HOME/.local/state/openshell/homebrew/tls" \
openshell-gateway --config "$PWD/gateway.local.toml"
```

The first command stops the background service so the foreground gateway can use
the standard port. The second command reuses the credentials and state created
by the recommended macOS installation, but loads this example's generated
`gateway.local.toml`. The gateway should stay in the foreground. TLS startup
followed by `Server listening` means it is ready.

Middleware registration is static. After regenerating `gateway.local.toml`,
stop this foreground process with `Ctrl-C` and run the second command again.

## 4. Create the sandbox and run Claude

In terminal 3:

```bash
openshell gateway add \
  https://127.0.0.1:17670 \
  --local \
  --name openshell

openshell status

openshell sandbox create \
  --name privacy-guard-email \
  --from base \
  --no-auto-providers \
  --policy "$PWD/policy.yaml" \
  -- env CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 claude
```

`gateway add` saves the endpoint as the CLI's active gateway and refreshes its
package-managed mTLS credentials. The registration continues to target the
normal local gateway after this walkthrough.

Choose Claude Code's subscription-account login and complete authentication.

OpenShell can start a sandbox in a degraded state when its supervisor cannot
reach Privacy Guard. Before sending a prompt, use another terminal to inspect
the startup logs:

```bash
openshell logs privacy-guard-email -n 100
```

Do not continue if they contain `Middleware connect failed` or
`CONFIG:DEGRADED`; correct the host address and recreate the sandbox first.

Then enter:

```text
Tell me something that rhymes with my email wendy@gmail.com
```

Privacy Guard should replace the address with `[email]` before Anthropic
receives it. The reply must not be used to verify redaction because the model
may refer to the placeholder or invent an example address.

In another terminal, inspect the finite recent log history:

```bash
openshell logs privacy-guard-email -n 100
```

Look for the `api.anthropic.com/v1/messages` request with `transformed:true` and
an email finding.

## Troubleshooting

- If gateway startup reports `middleware registration failed`, confirm Privacy
  Guard is still running and that `gateway.local.toml` contains the host's
  physical Ethernet or Wi-Fi IPv4 address.
- If a prompt returns `403 "middleware_failed"`, inspect the sandbox logs. A
  `Middleware connect failed` or `binding_not_described` entry means the sandbox
  supervisor could not reach the configured address. Regenerate
  `gateway.local.toml`, restart the foreground gateway, and recreate the sandbox.
- If logs report `middleware_timeout`, make sure the config does not contain a
  VPN address and that the host firewall permits sandbox traffic to port 50051.

## Change the behavior

Edit `policy.yaml` and change `on_finding.action` to `observe`, `block`, or
`redact`, then apply it without recreating the sandbox:

```bash
openshell policy set privacy-guard-email \
  --policy "$PWD/policy.yaml" \
  --wait
```

- `redact` sends `[email]` to the remote model provider.
- `observe` records the finding but sends the original email.
- `block` denies the request.

To reconnect later:

```bash
openshell sandbox connect privacy-guard-email
```

Then run `env CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 claude` inside the
sandbox.

## Cleanup

Exit Claude and the sandbox, then delete it:

```bash
openshell sandbox delete privacy-guard-email
```

Stop the foreground gateway with `Ctrl-C` in terminal 2, then restore the normal
background gateway:

```bash
brew services start openshell
```

Stop Privacy Guard with `Ctrl-C` in terminal 1. The example-specific gateway
configuration was never installed as the default configuration.

This example uses Claude Code because subscription prompts are sent in inspectable
HTTP request bodies. ChatGPT-subscription Codex currently sends prompts in
WebSocket frames, which this HTTP middleware cannot inspect.
