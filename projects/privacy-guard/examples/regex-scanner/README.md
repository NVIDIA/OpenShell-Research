# Built-in regex scanner walkthrough

This self-contained example runs Privacy Guard's built-in `RegexScanner`,
registers it with an OpenShell gateway, and uses a sandbox policy to redact an
email address and customer ID before the remote model provider receives them.
It is intended to be worked through by hand.

The example temporarily runs the installed OpenShell gateway with the config in
this directory. It does not create or modify `~/.config/openshell/gateway.toml`
or create a project-local state directory. It registers the standard local
gateway endpoint with the OpenShell CLI.

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
cd projects/privacy-guard/examples/regex-scanner
```

`uv` automatically discovers the Privacy Guard project in the parent
directories, so the commands do not need a `--project` option.

Use terminal 1 for Privacy Guard, terminal 2 for the foreground gateway,
terminal 3 for the agent, and another terminal for finite log checks.

## 1. Review the scanner configuration

`regex-scanner.yaml` configures two entities:

- `email` matches common email-shaped text.
- `customer-id` matches identifiers such as `CUST-12345678`.

The patterns are intentionally small and understandable. Treat them as a
starting point, not comprehensive production detection.

## 2. Generate the local gateway config

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
`gateway.local.toml`. Keep the gateway in the foreground. TLS startup followed
by `Server listening` means it is ready.

Middleware registration is static. After regenerating `gateway.local.toml`,
stop this foreground process with `Ctrl-C` and run the second command again.

## 5. Create the sandbox and run an agent

In terminal 3:

```bash
openshell gateway add \
  https://127.0.0.1:17670 \
  --local \
  --name openshell

openshell status
```

`gateway add` saves the endpoint as the CLI's active gateway and refreshes its
package-managed mTLS credentials. The registration continues to target the
normal local gateway after this walkthrough.

Choose either of the following paths. Both use the same sandbox name, so run
only one create command.

### Path A: Claude Code

```bash
openshell sandbox create \
  --name privacy-guard-regex \
  --from base \
  --no-auto-providers \
  --policy "$PWD/policy.yaml" \
  -- env CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 claude
```

Disabling nonessential Claude Code traffic keeps telemetry and error-reporting
batches out of this focused redaction exercise. Privacy Guard still processes
the model requests sent to `api.anthropic.com`.

Choose Claude Code's subscription-account login and complete authentication.

### Path B: Pi

This path supports a user-supplied OpenAI-compatible HTTPS endpoint on port 443.
Enter your endpoint URL, model ID, and API key after the three `=` signs:

```bash
PI_MODEL_ENDPOINT=
PI_MODEL_ID=
export PI_MODEL_API_KEY=
```

Keep a suffix such as `/v1` in `PI_MODEL_ENDPOINT` when the provider requires
it. The setup below derives the policy hostname from that URL.

Generate ignored local model and policy files:

```bash
: "${PI_MODEL_ENDPOINT:?Set PI_MODEL_ENDPOINT above}"
: "${PI_MODEL_ID:?Set PI_MODEL_ID above}"
: "${PI_MODEL_API_KEY:?Set PI_MODEL_API_KEY above}"

PI_MODEL_HOST=${PI_MODEL_ENDPOINT#*://}
PI_MODEL_HOST=${PI_MODEL_HOST%%/*}

sed \
  -e "s|REPLACE_WITH_MODEL_ENDPOINT|$PI_MODEL_ENDPOINT|" \
  -e "s|REPLACE_WITH_MODEL_ID|$PI_MODEL_ID|g" \
  pi-models.template.json > pi-models.local.json

sed "s/REPLACE_WITH_MODEL_HOST/$PI_MODEL_HOST/g" \
  policy.pi.template.yaml > policy.local.yaml
```

Create an OpenShell credential provider once. Passing the bare environment
variable name reads the value from the host without putting the key on the
command line:

```bash
openshell provider create \
  --name privacy-guard-model \
  --type generic \
  --credential PI_MODEL_API_KEY
```

If `privacy-guard-model` already exists, skip `provider create` and confirm it
with `openshell provider get privacy-guard-model`.

Create the sandbox with the generated model configuration and policy:

```bash
openshell sandbox create \
  --name privacy-guard-regex \
  --from pi \
  --provider privacy-guard-model \
  --no-auto-providers \
  --policy "$PWD/policy.local.yaml" \
  --no-git-ignore \
  --upload pi-models.local.json:/sandbox/.pi/agent/models.json \
  -- pi \
    --provider custom \
    --model "$PI_MODEL_ID"
```

The `pi` community sandbox contains the Pi coding agent in addition to the base
sandbox tools. These options open its interactive TUI with the custom model
already selected. OpenShell injects an opaque credential placeholder into the
sandbox and substitutes the real key only in the TLS-terminated request to
the configured endpoint. `privacy-guard-model` is the OpenShell credential
provider; `custom` is the separate provider name in Pi's model configuration.

### Confirm middleware startup

OpenShell can start a sandbox in a degraded state when its supervisor cannot
reach Privacy Guard. Before sending a prompt, use another terminal to inspect
the startup logs:

```bash
openshell logs privacy-guard-regex -n 100
```

Do not continue if they contain `Middleware connect failed` or
`CONFIG:DEGRADED`; correct the host address and recreate the sandbox first.

### Exercise the scanner

In the agent you chose, enter:

```text
Tell me something that rhymes with my email wendy@gmail.com and remember that my customer ID is CUST-12345678.
```

Privacy Guard should replace the values with `[email]` and `[customer-id]`
before the remote model provider receives the request. The reply must not be
used to verify redaction: models may refer to the placeholders or invent
example values.

In another terminal, inspect the finite recent log history:

```bash
openshell logs privacy-guard-regex -n 100
```

For Claude Code, look for the `api.anthropic.com/v1/messages` request. For Pi,
look for a request to the hostname assigned to `PI_MODEL_HOST`. The request
should have `transformed:true` and findings for both configured entities.

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

To investigate latency without logging request content, restart Privacy Guard
in terminal 1 with `--debug`:

```bash
uv run privacy-guard --debug regex \
  --config regex-scanner.yaml \
  --listen 0.0.0.0:50051
```

To compare the body received by Privacy Guard with the body it returns, use
`--debug-log-content` instead. This mode logs secrets by design:

```bash
uv run privacy-guard --debug-log-content regex \
  --config regex-scanner.yaml \
  --listen 0.0.0.0:50051
```

Match `stage=received` and `stage=forwarded` entries by `request_id`, and disable
content logging after capturing the failing request.

## Change the behavior

To change enforcement, edit `policy.yaml` for Claude Code or
`policy.pi.template.yaml` for Pi and set `on_finding.action` to `observe`,
`block`, or `redact`. For Pi, regenerate `policy.local.yaml` with the command in
Path B. Then apply the selected policy without recreating the sandbox:

```bash
POLICY_FILE=policy.yaml  # Use policy.local.yaml for Pi.
openshell policy set privacy-guard-regex \
  --policy "$PWD/$POLICY_FILE" \
  --wait
```

- `redact` sends `[email]` and `[customer-id]` to the remote model provider.
- `observe` records findings but sends the original values.
- `block` denies a request containing either configured entity.

To change detection, edit `regex-scanner.yaml`, stop Privacy Guard with `Ctrl-C`,
and run the terminal 1 command again. Scanner configuration is loaded only at
startup.

To reconnect later:

```bash
openshell sandbox connect privacy-guard-regex
```

Then run the same launch command you chose earlier:

```bash
env CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 claude
# or, in a Pi sandbox:
PI_MODEL_ID=
pi \
  --provider custom \
  --model "$PI_MODEL_ID"
```

## Cleanup

Exit the agent and the sandbox, then delete it:

```bash
openshell sandbox delete privacy-guard-regex
```

Stop the foreground gateway with `Ctrl-C` in terminal 2, then restore the normal
background gateway:

```bash
brew services start openshell
```

Stop Privacy Guard with `Ctrl-C` in terminal 1. The example-specific gateway
configuration was never installed as the default configuration.

This example uses Claude Code with Anthropic or Pi with an OpenAI-compatible
endpoint because their prompts are sent in inspectable HTTP request bodies.
ChatGPT-subscription Codex currently sends prompts in WebSocket frames, which
this HTTP middleware cannot inspect.
