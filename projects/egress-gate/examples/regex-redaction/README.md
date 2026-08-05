# Regex redaction

This example replaces email addresses and customer IDs in request bodies. The
OpenShell policy applies the built-in `regex` gate to requests for one provider
endpoint.

Run these commands from `projects/egress-gate/examples/regex-redaction/`.

## Test the gate

Inspect the installed gates, then test the standalone policy against two saved
requests:

```bash
uv run egress-gate gates list
uv run egress-gate evaluate \
  --policy egress-gate-config.yaml \
  --cases cases.yaml
```

## Run it with OpenShell

Start Egress Gate in one terminal. The working directory contains the pattern
catalog referenced by `policy.yaml`.

```bash
uv run egress-gate serve --listen 0.0.0.0:50051 --timeout-seconds 4
```

In another terminal, add the registration to your default OpenShell gateway
configuration. Replace `YOUR_HOST_IPV4` with a non-loopback address that the
gateway and sandbox supervisors can reach.

```bash
uv run egress-gate add-gateway-registration \
  --host-ip YOUR_HOST_IPV4 \
  --name egress-gate-redaction \
  --port 50051
```

Restart the OpenShell gateway, then create a sandbox with `policy.yaml`. The
policy refers to the same `egress-gate-redaction` registration name.

To remove the example registration from the default gateway configuration,
run this command and restart the gateway:

```bash
uv run egress-gate remove-gateway-registration \
  --name egress-gate-redaction
```

## What the policy does

The gate uses `scan.kind: body` with `action.kind: replace`. It strictly
decodes the body as UTF-8, finds catalog matches, and requests a body
replacement. Egress Gate applies that mutation before the request continues.

Body scans also support `detect` and `deny`. The same gate can detect or deny
matches in the path, query, or selected header values.
