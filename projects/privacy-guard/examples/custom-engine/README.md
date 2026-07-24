# Custom engine end-to-end example

This example implements `KeywordEngine`, registers an operator-owned
`KeywordAnalysisTool`, runs that custom registry through the standard Privacy
Guard CLI, and supplies an OpenShell policy for an end-to-end replacement.

The analyzer is intentionally small. Its purpose is to show the complete
integration boundary that a production adapter for a library or service such as
NeMo Anonymizer would use:

- `KeywordEngineConfig` and `TokenReplacement` contain policy-owned behavior.
- `KeywordAnalysisTool` is an operator-created dependency held by the
  `KeywordEngineResources` bundle and never appears in policy.
- `KeywordEngine` translates tool matches into `EntityDetection` objects and
  implements detection and replacement.
- `create_registry()` registers the implementation and resource, finalizes the
  exact Pydantic policy union, and returns the application-scoped registry.

Run commands from this directory:

```bash
cd projects/privacy-guard/examples/custom-engine
```

## Inspect the custom installation

The registry-factory option uses the same custom registry for discovery,
schema generation, and serving:

```bash
uv run privacy-guard \
  --registry-factory custom_engine:create_registry \
  engines

uv run privacy-guard \
  --registry-factory custom_engine:create_registry \
  schema
```

The engine listing should contain `keyword-tool`, and the schema should contain
its exact `KeywordEngineConfig` fields. Registry factories execute operator
Python code in the Privacy Guard process; use only trusted modules.

## Run Privacy Guard

In terminal 1:

```bash
uv run privacy-guard \
  --registry-factory custom_engine:create_registry \
  serve \
  --listen 0.0.0.0:50051
```

The development server uses unauthenticated plaintext gRPC and receives
potentially sensitive request bodies. Restrict port 50051 to the host and
OpenShell sandbox network.

## Connect OpenShell

Enter the host's physical Ethernet or Wi-Fi IPv4 address, then generate the
example-specific gateway configuration:

```bash
YOUR_HOST_IP=
sed "s/REPLACE_WITH_HOST_IP/$YOUR_HOST_IP/" gateway.toml > gateway.local.toml
grep grpc_endpoint gateway.local.toml
```

Do not use `127.0.0.1`, a VPN address, or `host.openshell.internal`; the sandbox
supervisor must be able to reach the middleware.

In terminal 2, stop the background gateway and run the installed gateway with
the example configuration. These paths target the recommended macOS Homebrew
installation:

```bash
brew services stop openshell

OPENSHELL_LOCAL_TLS_DIR="$HOME/.local/state/openshell/homebrew/tls" \
openshell-gateway --config "$PWD/gateway.local.toml"
```

In terminal 3, register that local gateway and create the sandbox:

```bash
openshell gateway add \
  https://127.0.0.1:17670 \
  --local \
  --name openshell

openshell sandbox create \
  --name privacy-guard-custom-engine \
  --from base \
  --no-auto-providers \
  --policy "$PWD/policy.yaml" \
  -- env CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 claude
```

After authenticating Claude Code, enter:

```text
Tell me something that rhymes with the confidential name Project Cobalt
```

Privacy Guard should send `[confidential-project]` instead of `Project Cobalt`
to the provider. Verify the middleware result from finite recent logs, rather
than relying on the model's reply:

```bash
openshell logs privacy-guard-custom-engine -n 100
```

Look for the `api.anthropic.com/v1/messages` request with `transformed:true` and
a `confidential-project (project-names)` finding.

## Cleanup

Exit Claude and delete the sandbox:

```bash
openshell sandbox delete privacy-guard-custom-engine
```

Stop the foreground gateway and Privacy Guard with `Ctrl-C`, then restore the
normal gateway:

```bash
brew services start openshell
```
