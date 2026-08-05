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

Start Egress Gate with content-safe debug diagnostics in one terminal. The
working directory contains the pattern catalog referenced by `policy.yaml`.

```bash
uv run egress-gate --debug serve \
  --listen 0.0.0.0:50051 \
  --timeout-seconds 4
```

In another terminal, add the registration to your default OpenShell gateway
configuration. Replace `YOUR_HOST_IPV4` with a non-loopback address that the
gateway and sandbox supervisors can reach.

```bash
uv run egress-gate add-gateway-registration \
  --host-ip YOUR_HOST_IPV4 \
  --name eg-regex \
  --port 50051
```

Restart the OpenShell gateway, then create a sandbox and launch Claude Code:

```bash
openshell sandbox create \
  --name eg-regex \
  --from base \
  --no-auto-providers \
  --policy policy.yaml \
  -- env CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 claude
```

This command uses the base sandbox image, prevents OpenShell from creating or
attaching a provider, and starts Claude Code with nonessential traffic disabled.
The policy can therefore omit telemetry and error-reporting endpoints.

On the first run, complete Claude Code's browser sign-in from inside the
sandbox. The session uses your Claude subscription directly; OpenShell does not
attach an Anthropic API-key provider.

At the Claude prompt, enter:

```text
Reply with exactly this text: alice@example.com CUST-12345678
```

Claude must not receive the original identifiers. Its response should contain
`[email]` and `[customer-id]` instead. The Egress Gate terminal also records an
allow decision with `finding_count=2`, without logging request content. These
two observations confirm that OpenShell called Egress Gate and applied the
replacement before it sent the request to Claude.

Exit Claude Code, then delete the sandbox when the test is complete:

```bash
openshell sandbox delete eg-regex
```

To remove the example registration from the default gateway configuration,
run this command and restart the gateway:

```bash
uv run egress-gate remove-gateway-registration \
  --name eg-regex
```

OpenShell names used by this example have a 19-character limit. The chosen
names stay within that limit.

## What the policy does

The gate uses `scan.kind: body` with `action.kind: replace`. It strictly
decodes the body as UTF-8, finds catalog matches, and requests a body
replacement. Egress Gate applies that mutation before the request continues.

Body scans also support `detect` and `deny`. The same gate can detect or deny
matches in the path, query, or selected header values.
