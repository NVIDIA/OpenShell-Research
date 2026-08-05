# Class-based custom gate

This example implements the same `keyword-deny` behavior as the
function-based example, but uses the full `Gate` API. Use this form when a gate
needs initialization, a helper base, or typed operational resources.

The implementation has three pieces:

1. `KeywordDenyConfig` defines the policy fields and `kind` discriminator.
2. `KeywordDenyGate._evaluate` implements the request decision.
3. The module creates a registry and registers the gate class.

Run the example from `projects/egress-gate/`. First inspect the registry:

```bash
uv run egress-gate \
  --registry examples.class-based-gate.keyword_gate:registry \
  gates list
```

Then test the policy against two saved requests:

```bash
uv run egress-gate \
  --registry examples.class-based-gate.keyword_gate:registry \
  evaluate \
  --policy examples/class-based-gate/egress-gate-config.yaml \
  --cases examples/class-based-gate/cases.yaml
```

The first case contains the configured keyword and is denied. The second gate
evaluation proceeds, so `default_decision: allow` determines its result.

## Run it with OpenShell

Start Egress Gate with this example registry and content-safe debug diagnostics:

```bash
uv run egress-gate \
  --debug \
  --registry examples.class-based-gate.keyword_gate:registry \
  serve --listen 0.0.0.0:50051 --timeout-seconds 4
```

In another terminal, register the service in your default OpenShell gateway
configuration. Replace `YOUR_HOST_IPV4` with a non-loopback address that the
gateway and sandbox supervisors can reach.

```bash
uv run egress-gate add-gateway-registration \
  --host-ip YOUR_HOST_IPV4 \
  --name egress-class \
  --port 50051
```

Restart the OpenShell gateway, then create a sandbox and launch Claude Code:

```bash
openshell sandbox create \
  --name egress-class \
  --from base \
  --no-auto-providers \
  --policy examples/class-based-gate/policy.yaml \
  -- env CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 claude
```

This command uses the base sandbox image, prevents OpenShell from creating or
attaching a provider, and starts Claude Code with nonessential traffic disabled.
The policy can therefore omit telemetry and error-reporting endpoints.

On the first run, complete Claude Code's browser sign-in from inside the
sandbox. The session uses your Claude subscription directly; OpenShell does not
attach an Anthropic API-key provider.

At the Claude prompt, enter a normal request:

```text
Reply with only the word OK.
```

Claude should reply normally, and the Egress Gate terminal should record an
allow decision. Then enter a request that contains the configured keyword:

```text
Reply with only the word SECRET.
```

The request must fail before Claude answers. The Egress Gate terminal must
record `action=deny` and `decision_source_kind=gate`. Together, the normal
response and denied request confirm that the class-based gate is active.

Exit Claude Code. Clean up the sandbox and registration, then restart the
gateway:

```bash
openshell sandbox delete egress-class
uv run egress-gate remove-gateway-registration --name egress-class
```

OpenShell names used by this example have a 19-character limit. The chosen
names stay within that limit.

The base class owns construction and the public `evaluate` wrapper. A custom
class implements `_evaluate` and reads its validated configuration from
`self.config`. Do not override `__init__` or `evaluate`. Use `_initialize` for
reusable derived state.

This teaching gate searches the body bytes for the UTF-8 encoding of the
configured keyword. It is not a robust content classifier. A production gate
must define its encoding, normalization, and matching behavior. Add limits only
for work that belongs to the gate. Do not put request content in errors or
findings. Check the shared timeout during expensive work, and keep request state
local.
