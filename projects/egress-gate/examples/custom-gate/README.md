# Function-based custom gate

This example adds a `keyword-deny` gate in one Python file. If the configured
keyword occurs in the request body, the gate denies the request. Otherwise, it
returns `proceed`, and the pipeline continues.

The implementation has three pieces:

1. `KeywordDenyConfig` defines the exact policy fields and the stable
   `kind: keyword-deny` discriminator.
2. `registry.gate` turns the typed `keyword_deny` function into a standard
   resource-free gate type and adds it to the application registry.
3. The CLI loads that module-owned registry directly.

Run the example from `projects/egress-gate/`. First confirm that the custom
gate is installed in this registry:

```bash
uv run egress-gate \
  --registry examples.custom-gate.keyword_gate:registry \
  gates list
```

Then test the policy against two saved requests:

```bash
uv run egress-gate \
  --registry examples.custom-gate.keyword_gate:registry \
  evaluate \
  --policy examples/custom-gate/egress-gate-config.yaml \
  --cases examples/custom-gate/cases.yaml
```

The executable resolves the explicit `module:attribute` reference from the
working directory. The attribute can contain a registry or a zero-argument
registry factory. An installed custom-gate package works the same way.

The first case contains the configured keyword and is denied. The second gate
evaluation proceeds, so `default_decision: allow` determines its result.

## Run it with OpenShell

Start Egress Gate with this example registry and content-safe debug diagnostics:

```bash
uv run egress-gate \
  --debug \
  --registry examples.custom-gate.keyword_gate:registry \
  serve --listen 0.0.0.0:50051 --timeout-seconds 4
```

Before you change the gateway configuration, stop any running OpenShell
gateways that use it. A running gateway does not reload middleware
registrations.

In another terminal, register the service in your default gateway
configuration. Replace `YOUR_HOST_IPV4` with a non-loopback address that the
gateway and sandbox supervisors can reach.

```bash
uv run egress-gate add-gateway-registration \
  --host-ip YOUR_HOST_IPV4 \
  --name egress-function \
  --port 50051
```

Start the OpenShell gateway again with the same command or service manager that
you normally use. Then create a sandbox and launch Claude Code:

```bash
openshell sandbox create \
  --name egress-function \
  --from base \
  --no-auto-providers \
  --policy examples/custom-gate/policy.yaml \
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
response and denied request confirm that the custom gate is active.

Exit Claude Code and delete the sandbox. Stop any running OpenShell gateways
before you remove the registration. Then start the gateways again:

```bash
openshell sandbox delete egress-function
uv run egress-gate remove-gateway-registration --name egress-function
```

OpenShell names used by this example have a 19-character limit. The chosen
names stay within that limit.

This teaching gate searches the body bytes for the UTF-8 encoding of the
configured keyword. It is not a robust content classifier. The pipeline
processor already checks the `HttpRequest` limits; the gate does not repeat
those checks.

The decorator is a helper for small, stateless gates. See the runnable
[`class-based-gate`](../class-based-gate/) example when a gate needs reusable
initialization, a helper base, or typed operational resources.

A production gate must define its encoding, normalization, and matching
behavior. Add limits only for work that belongs to the gate. Do not put request
content in errors or findings. Check the shared timeout during expensive work,
and keep request state local.
