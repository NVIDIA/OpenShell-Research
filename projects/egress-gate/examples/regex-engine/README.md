# RegexEngine end-to-end example

This example runs Egress Gate's built-in `RegexEngine` through OpenShell. The
final check sends a Claude Code request containing an email address and customer
ID, then verifies that OpenShell forwards `[email]` and `[customer-id]`.

Egress Gate does not ship authoritative regex presets. Copy and adapt
`patterns.yaml` for the data you actually need to identify, and test every
pattern against representative matches, non-matches, and worst-case inputs
before deployment.

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

## Start Egress Gate

In terminal 1, from this example directory:

```bash
cd projects/egress-gate/examples/regex-engine
uv run --locked egress-gate serve --listen 0.0.0.0:50051
```

Leave this terminal running. The development server is unauthenticated
plaintext gRPC and receives potentially sensitive request bodies. Binding to
`0.0.0.0` is necessary for the sandbox supervisor to reach it, but port 50051
must remain restricted to the host and trusted sandbox network.

## Configure and start the gateway

Choose a non-loopback host IPv4 address that both the gateway and sandbox
supervisor can reach.

In terminal 2, return to the example directory. Replace `YOUR_HOST_IPV4` with
the address you selected, then update the default gateway configuration:

```bash
cd projects/egress-gate/examples/regex-engine
uv run egress-gate add-gateway-registration \
  --host-ip YOUR_HOST_IPV4 \
  --name egress-gate-regex
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
  --name egress-gate-regex \
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

Egress Gate should send `[email]` and `[customer-id]` instead of the original
identifiers to the provider.

## Verify the middleware result

From another host terminal:

```bash
openshell logs egress-gate-regex -n 100 --source sandbox
```

Look for the `api.anthropic.com/v1/messages` request with `transformed:true`,
plus `email (identifiers)` and `customer-id (identifiers)` findings. Findings
must not contain the matched email address or customer ID.

## Cleanup

Exit Claude and delete the sandbox:

```bash
openshell sandbox delete egress-gate-regex
```

Stop Egress Gate with `Ctrl-C`, then stop the gateway before removing the
example registration:

```bash
# macOS with Homebrew
brew services stop openshell

# Linux with a Debian or RPM package
systemctl --user stop openshell-gateway

uv run egress-gate remove-gateway-registration \
  --name egress-gate-regex
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

- Sandbox creation reports unavailable middleware: confirm terminal 1 is still
  running, check the IP in the default gateway configuration, and allow trusted
  sandbox traffic to host port 50051.
- Policy or middleware registration fields are rejected: confirm that
  `openshell` and `openshell-gateway` use compatible versions. If the error
  remains, use the tested `v0.0.90` release.
