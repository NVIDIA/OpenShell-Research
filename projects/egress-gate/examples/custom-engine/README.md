# Custom engine end-to-end example

This example implements and registers `KeywordEngine` in one Python file, then
runs it through Egress Gate and OpenShell. The final check sends a Claude Code
request containing `Project Cobalt` and verifies that Egress Gate reports the
configured confidential-project finding.

The implementation is intentionally compact but complete:

- `KeywordEngineConfig` defines the policy-owned entity and keyword.
- `KeywordEngine._run()` finds every literal occurrence and returns detections.
- `TextProcessingResult.from_detections()` bounds the lazy detection stream.
- `create_registry()` includes the built-in engines and registers the custom
  implementation for every CLI command.

The base engine wrapper validates strategy support, input, timeout, spans,
output size, mutation behavior, and result cardinality. Custom engines add
their own checks only when an underlying library or service has a unique
low-level requirement.

## Prerequisites

This walkthrough was validated with OpenShell `v0.0.90`, the version recorded
in Egress Gate's `.openshell-middleware-manifest.json`. A later OpenShell
release can also work if it supports the same supervisor middleware contract
and policy schema.

Before you start, install:

- Python 3.11 or newer and `uv` 0.11 or newer
- [OpenShell](https://github.com/NVIDIA/OpenShell) `v0.0.90` or a later
  compatible version

The gateway lifecycle commands below cover macOS Homebrew and Linux Debian/RPM
installations. For another deployment, use its equivalent gateway commands.

## Stop the local gateway

First, check the local gateway:

```bash
openshell status
```

If the gateway is running, stop it before you change its configuration. Use the
command for your system:

```bash
# macOS with Homebrew
brew services stop openshell

# Linux with a Debian or RPM package
systemctl --user stop openshell-gateway
```

Run every command below from this example directory. In each new terminal,
repeat the `cd` command:

```bash
cd projects/egress-gate/examples/custom-engine
```

## Inspect the custom installation

The console script does not automatically add its current directory to Python's
module path. Export it explicitly so the local example modules are importable:

```bash
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"
```

Use the same custom registry for discovery, schema generation, and serving:

```bash
uv run --locked egress-gate \
  --registry-factory custom_engine:create_registry \
  engines

uv run egress-gate \
  --registry-factory custom_engine:create_registry \
  configuration-schema
```

The first command should print the built-in `regex` row with `detect,replace`
and the custom `keyword-tool` row with `detect`. The schema should contain both
`RegexEngineConfig` and `KeywordEngineConfig`; the latter includes its exact
`entity` and `keyword` fields. Registry factories execute operator Python code
in the Egress Gate process; use only trusted modules.

`egress-gate-config.yaml` shows the standalone engine configuration. OpenShell
does not load that file separately; `policy.yaml` contains the same configuration
inline under `network_middlewares`.

## Start Egress Gate

In terminal 1, enter this example directory, export `PYTHONPATH` again, and run:

```bash
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"

uv run egress-gate \
  --registry-factory custom_engine:create_registry \
  serve \
  --listen 0.0.0.0:50051
```

Leave this terminal running. The development server is unauthenticated
plaintext gRPC and receives potentially sensitive request bodies. Binding to
`0.0.0.0` is necessary for the sandbox supervisor to reach it, but port 50051
must remain restricted to the host and trusted sandbox network.

## Configure and start the gateway

Choose a non-loopback host IPv4 address that both the gateway and sandbox
supervisor can use to reach Egress Gate.

In terminal 2, return to the example directory. Replace `YOUR_HOST_IPV4` with
the address you selected, then update the default gateway configuration:

```bash
cd projects/egress-gate/examples/custom-engine
uv run egress-gate add-gateway-registration \
  --host-ip YOUR_HOST_IPV4 \
  --name egress-gate-custom-engine
```

Do not use `127.0.0.1`, a VPN address, or `host.openshell.internal`. The gateway
and sandbox supervisor must both be able to reach the configured endpoint.

Next, use the command for your system to start the gateway in the background:

```bash
# macOS with Homebrew
brew services start openshell

# Linux with a Debian or RPM package
systemctl --user start openshell-gateway
```

## Verify OpenShell and create the sandbox

In terminal 3, from this example directory:

```bash
openshell status
```

Do not continue until status reports that the gateway is connected.

This walkthrough starts Claude Code in the sandbox. To use a different harness,
replace everything after `--` with its command. Then create the sandbox:

```bash
openshell sandbox create \
  --name egress-gate-custom-engine \
  --from base \
  --no-auto-providers \
  --policy "$PWD/policy.yaml" \
  -- env CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 claude
```

Sandbox creation validates the external middleware registration and the exact
`KeywordEngineConfig` embedded in the policy. A successful creation is therefore
also the end-to-end configuration check.

After authenticating Claude Code, enter:

```text
Tell me something that rhymes with the confidential name Project Cobalt
```

This detection-only example leaves the request body unchanged and records the
finding before the provider request continues.

## Verify the middleware result

Do not infer success from the model's wording. From another host terminal,
inspect a finite recent log window:

```bash
openshell logs egress-gate-custom-engine -n 100 --source sandbox
```

Look for the `api.anthropic.com/v1/messages` request with `transformed:false`
and a `confidential-project (project-names)` finding. The raw confidential
value must not appear in middleware findings.

## Cleanup

Exit Claude and delete the sandbox:

```bash
openshell sandbox delete egress-gate-custom-engine
```

Stop Egress Gate with `Ctrl-C`, then stop the gateway before removing the
example registration:

```bash
# macOS with Homebrew
brew services stop openshell

# Linux with a Debian or RPM package
systemctl --user stop openshell-gateway

uv run egress-gate remove-gateway-registration \
  --name egress-gate-custom-engine
```

Restart the gateway with the command for your system, then verify its
connection:

```bash
# macOS with Homebrew
brew services start openshell

# Linux with a Debian or RPM package
systemctl --user start openshell-gateway

openshell status
```

## Troubleshooting

- `registry factory could not be loaded`: export `PYTHONPATH` in the terminal
  running `egress-gate`.
- Sandbox creation reports unavailable middleware: confirm terminal 1 is still
  running, check the IP in the default gateway configuration, and allow trusted
  sandbox traffic to host port 50051.
- Policy or middleware registration fields are rejected: confirm that
  `openshell` and `openshell-gateway` use compatible versions. If the error
  remains, use the tested `v0.0.90` release.
