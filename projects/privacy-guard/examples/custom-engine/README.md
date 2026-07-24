# Custom engine end-to-end example

This example implements `KeywordEngine`, registers an operator-owned
`KeywordAnalysisTool`, and runs the custom engine through Privacy Guard and
OpenShell. The final check sends a Claude Code request containing
`Project Cobalt` and verifies that OpenShell forwards `[confidential-project]`
instead.

The analyzer is intentionally small. Its purpose is to show the complete
integration boundary that a production adapter for a library or service such as
NeMo Anonymizer would use:

- `KeywordEngineConfig` and `TokenReplacement` contain policy-owned behavior.
- `KeywordAnalysisTool` is an operator-created dependency held by the
  `KeywordEngineResources` bundle and never appears in policy.
- `KeywordEngine` translates tool matches into `EntityDetection` objects and
  implements detection and replacement.
- `privacy_guard_app.py` is deployment-owned wiring. Its `create_registry()`
  selects the installed engine, creates its runtime resource, and returns the
  finalized application-scoped registry.

An engine author implements only the types in `custom_engine.py`; registration
is not part of the engine contract. The deployment owner performs the small
amount of explicit application assembly in `privacy_guard_app.py`. Privacy Guard
needs that complete inventory at startup to build the exact Pydantic policy
union and inject operator-owned resources.

## Prerequisites

This walkthrough targets the protocol and policy schema in OpenShell `v0.0.90`,
the version recorded in Privacy Guard's `.openshell-middleware-manifest.json`.
Other OpenShell releases may have different middleware configuration or CLI
syntax.

Before starting, have:

- Python 3.11 or newer and `uv` 0.11 or newer
- OpenShell `v0.0.90`, installed with its package-managed local gateway
- a running Docker or Podman backend supported by OpenShell
- Claude Code subscription access if you want to perform the final provider call

The gateway lifecycle commands below cover macOS Homebrew and Linux Debian/RPM
installations. Snap, Kubernetes, remote, and custom gateway deployments need
equivalent service-management, TLS, and middleware-routing configuration.

Confirm the important versions:

```bash
uv --version
openshell --version
openshell-gateway --version
```

Run every command below from this example directory. In each new terminal,
repeat the `cd` command:

```bash
cd projects/privacy-guard/examples/custom-engine
uv sync --locked
```

## Inspect the custom installation

The console script does not automatically add its current directory to Python's
module path. Export it explicitly so the local example modules are importable:

```bash
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"
```

Use the same custom registry for discovery, schema generation, and serving:

```bash
uv run privacy-guard \
  --registry-factory privacy_guard_app:create_registry \
  engines

uv run privacy-guard \
  --registry-factory privacy_guard_app:create_registry \
  schema
```

The first command should print one `keyword-tool` row with `detect,replace`.
The schema should contain `KeywordEngineConfig`, including its exact `entity`,
`keyword`, and `replacement` fields. Registry factories execute operator Python
code in the Privacy Guard process; use only trusted modules.

`privacy-guard-config.yaml` shows the standalone engine configuration. OpenShell
does not load that file separately; `policy.yaml` contains the same configuration
inline under `network_middlewares`.

## Start Privacy Guard

In terminal 1, enter this example directory, export `PYTHONPATH` again, and run:

```bash
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"

uv run privacy-guard \
  --registry-factory privacy_guard_app:create_registry \
  serve \
  --listen 0.0.0.0:50051
```

Leave this terminal running. The development server is unauthenticated
plaintext gRPC and receives potentially sensitive request bodies. Binding to
`0.0.0.0` is necessary for the sandbox supervisor to reach it, but port 50051
must remain restricted to the host and trusted sandbox network.

## Create the gateway configuration

The gateway and sandbox supervisor must both be able to reach Privacy Guard.
Find a non-loopback IPv4 address for the physical Ethernet or Wi-Fi interface:

```bash
# macOS examples; use the interface that is actually connected.
ipconfig getifaddr en0
ipconfig getifaddr en1

# Linux: inspect the addresses and choose the LAN address.
hostname -I
```

In terminal 2, from this example directory, assign the selected address and
generate the local gateway configuration:

```bash
YOUR_HOST_IP=YOUR_HOST_IPV4
if [ "$YOUR_HOST_IP" = "YOUR_HOST_IPV4" ] || [ "$YOUR_HOST_IP" = "127.0.0.1" ]; then
  echo "Set YOUR_HOST_IP to a non-loopback IPv4 address"
else
  sed "s/REPLACE_WITH_HOST_IP/$YOUR_HOST_IP/" gateway.toml > gateway.local.toml
  grep grpc_endpoint gateway.local.toml
fi
```

Replace `YOUR_HOST_IPV4` with the address you selected. Do not use
`127.0.0.1`, a VPN address, or `host.openshell.internal`: the foreground gateway
process and the sandbox supervisor must both be able to resolve and reach the
configured endpoint.

## Restart the local gateway with middleware enabled

The installed gateway does not dynamically reload middleware registrations.
Stop its package-managed service, then run the same gateway binary in the
foreground with `gateway.local.toml`.

Run the command for your host:

```bash
# macOS/Homebrew
brew services stop openshell

# Linux Debian/RPM package
systemctl --user stop openshell-gateway
```

Still in terminal 2, select the package-managed TLS directory for your host and
start the gateway:

```bash
# macOS/Homebrew
export OPENSHELL_LOCAL_TLS_DIR="$HOME/.local/state/openshell/homebrew/tls"

# Linux Debian/RPM package
export OPENSHELL_LOCAL_TLS_DIR="$HOME/.local/state/openshell/tls"

openshell-gateway --config "$PWD/gateway.local.toml"
```

Run only one `export` line. Leave the foreground gateway running.

## Verify OpenShell and create the sandbox

The package installer normally creates an `openshell` gateway registration.
Reuse it; attempting to add another gateway with that name fails because it
already exists.

In terminal 3, from this example directory:

```bash
openshell gateway select openshell
openshell status
```

Do not continue until status reports the foreground gateway as connected.
Then create the sandbox:

```bash
openshell sandbox create \
  --name privacy-guard-custom-engine \
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

Privacy Guard should send `[confidential-project]` instead of `Project Cobalt`
to the provider.

## Verify the middleware result

Do not infer success from the model's wording. From another host terminal,
inspect a finite recent log window:

```bash
openshell logs privacy-guard-custom-engine -n 100 --source sandbox
```

Look for the `api.anthropic.com/v1/messages` request with `transformed:true` and
a `confidential-project (project-names)` finding. The raw confidential value
must not appear in middleware findings.

## Cleanup

Exit Claude and delete the sandbox:

```bash
openshell sandbox delete privacy-guard-custom-engine
```

Stop the foreground gateway and Privacy Guard with `Ctrl-C`. Restore the
package-managed gateway with the command for your host:

```bash
# macOS/Homebrew
brew services start openshell

# Linux Debian/RPM package
systemctl --user start openshell-gateway
```

Verify recovery and remove the generated configuration:

```bash
openshell gateway select openshell
openshell status
rm gateway.local.toml
```

## Troubleshooting

- `registry factory could not be loaded`: export `PYTHONPATH` in the terminal
  running `privacy-guard`.
- Port 17670 is already in use: the package-managed gateway was not stopped.
- The foreground gateway cannot find certificates: use the TLS directory for
  your platform exactly as shown above.
- Sandbox creation reports unavailable middleware: confirm terminal 1 is still
  running, check the IP in `gateway.local.toml`, and allow trusted sandbox
  traffic to host port 50051.
- Policy or middleware registration fields are rejected: confirm both
  `openshell` and `openshell-gateway` are from `v0.0.90`.
