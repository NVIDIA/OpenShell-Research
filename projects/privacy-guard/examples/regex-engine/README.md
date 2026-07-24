# RegexEngine end-to-end example

This example runs Privacy Guard's built-in `RegexEngine` through OpenShell. The
final check sends a Claude Code request containing an email address and customer
ID, then verifies that OpenShell forwards `[email]` and `[customer-id]`.

Privacy Guard does not ship authoritative regex presets. Copy and adapt
`patterns.yaml` for the data you actually need to identify, and test every
pattern against representative matches, non-matches, and worst-case inputs
before deployment.

## Prerequisites

This walkthrough targets OpenShell `v0.0.90`, the version recorded in Privacy
Guard's `.openshell-middleware-manifest.json`. Other releases may use different
middleware configuration or CLI syntax.

Before starting, have:

- Python 3.11 or newer and `uv` 0.11 or newer
- OpenShell `v0.0.90`, installed with its package-managed local gateway
- a running Docker or Podman backend supported by OpenShell
- Claude Code subscription access if you want to perform the final provider call

The gateway lifecycle commands below cover macOS Homebrew and Linux Debian/RPM
installations. Snap, Kubernetes, remote, and custom gateway deployments need
equivalent service-management, TLS, and middleware-routing configuration.

Confirm the versions:

```bash
uv --version
openshell --version
openshell-gateway --version
```

Run every command below from this example directory. In each new terminal,
repeat the `cd` command:

```bash
cd projects/privacy-guard/examples/regex-engine
uv sync --locked
```

## Inspect the built-in installation

```bash
uv run privacy-guard engines
uv run privacy-guard schema
```

The first command should print one `regex` row with `detect,replace`. The schema
should contain `RegexEngineConfig`, `RegexPatternCatalog`, and
`RegexReplacement`.

`privacy-guard-config.yaml` is the standalone Privacy Guard configuration.
`policy.yaml` contains the same configuration inline under
`network_middlewares`, which is the form OpenShell sends to the middleware.
Both configurations pass `patterns.yaml` directly as the complete
`pattern_catalog`. Privacy Guard resolves that relative path from its working
directory, safely loads the YAML file, and validates it with the same
`RegexPatternCatalog` model used for an inline catalog.

Catalog paths must be relative `.yaml` or `.yml` paths beneath Privacy Guard's
working directory. Absolute paths, `..` traversal, and symlinks are rejected.
This example therefore starts Privacy Guard from this directory. Restart the
service from the same directory whenever it is stopped.

## Start Privacy Guard

In terminal 1, from this example directory:

```bash
uv run privacy-guard serve --listen 0.0.0.0:50051
```

Leave this terminal running. The development server is unauthenticated
plaintext gRPC and receives potentially sensitive request bodies. Binding to
`0.0.0.0` is necessary for the sandbox supervisor to reach it, but port 50051
must remain restricted to the host and trusted sandbox network.

## Create the gateway configuration

Find a non-loopback IPv4 address for the physical Ethernet or Wi-Fi interface:

```bash
# macOS examples; use the interface that is actually connected.
ipconfig getifaddr en0
ipconfig getifaddr en1

# Linux: inspect the addresses and choose the LAN address.
hostname -I
```

In terminal 2, from this example directory, assign the selected address:

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
Stop its package-managed service with the command for your host:

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

In terminal 3, from this example directory:

```bash
openshell gateway select openshell
openshell status
```

Do not continue until status reports the foreground gateway as connected.
Then create the sandbox:

```bash
openshell sandbox create \
  --name privacy-guard-regex \
  --from base \
  --no-auto-providers \
  --policy "$PWD/policy.yaml" \
  -- env CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 claude
```

Sandbox creation validates the external middleware registration and the exact
`RegexEngineConfig` embedded in the policy.

After authenticating Claude Code, enter:

```text
Draft a short greeting for user@example.com about customer CUST-12345678.
```

Privacy Guard should send `[email]` and `[customer-id]` instead of the original
identifiers to the provider.

## Verify the middleware result

From another host terminal:

```bash
openshell logs privacy-guard-regex -n 100 --source sandbox
```

Look for the `api.anthropic.com/v1/messages` request with `transformed:true`,
plus `email (identifiers)` and `customer-id (identifiers)` findings. Findings
must not contain the matched email address or customer ID.

## Cleanup

Exit Claude and delete the sandbox:

```bash
openshell sandbox delete privacy-guard-regex
```

Stop the foreground gateway and Privacy Guard with `Ctrl-C`. Restore the
package-managed gateway with the command for your host:

```bash
# macOS/Homebrew
brew services start openshell

# Linux Debian/RPM package
systemctl --user start openshell-gateway
```

Verify recovery and remove the generated gateway file:

```bash
openshell gateway select openshell
openshell status
rm gateway.local.toml
```

## Troubleshooting

- Port 17670 is already in use: the package-managed gateway was not stopped.
- The foreground gateway cannot find certificates: use the TLS directory for
  your platform exactly as shown above.
- Sandbox creation reports unavailable middleware: confirm terminal 1 is still
  running, check the IP in `gateway.local.toml`, and allow trusted sandbox
  traffic to host port 50051.
- Policy or middleware registration fields are rejected: confirm both
  `openshell` and `openshell-gateway` are from `v0.0.90`.
