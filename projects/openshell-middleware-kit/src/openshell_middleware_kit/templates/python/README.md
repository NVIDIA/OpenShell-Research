# __PROJECT_NAME__

A Python OpenShell supervisor middleware starter. It is pinned to the OpenShell
contract recorded in `middleware-dev-manifest.json` and starts as a pass-through:
valid pre-credentials HTTP requests are allowed without mutation.

## Develop

Install [uv](https://docs.astral.sh/uv/), then run the local checks:

```sh
uv sync --locked
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest
uv build
```

Start the middleware on loopback for local development:

```sh
uv run __DISTRIBUTION_NAME__ --listen 127.0.0.1:50051
```

The server implementation is in `src/__PACKAGE_NAME__/server.py`. Extend
`validate_config` and `evaluate_http_request` with your policy and request
handling. Keep transport adaptation at this boundary and move substantial
domain logic into separate modules.

## Connect OpenShell

When the gateway or supervisors use another network namespace, explicitly bind
the development server to a reachable interface:

```sh
uv run __DISTRIBUTION_NAME__ --listen 0.0.0.0:50051
```

Register the running service in the gateway configuration:

```toml
[[openshell.supervisor.middleware]]
name = "__SERVICE_NAME__"
grpc_endpoint = "http://<supervisor-reachable-host>:50051"
max_body_bytes = 4194304
timeout = "500ms"
```

Replace `<supervisor-reachable-host>` with a host IP or DNS name reachable from
both the gateway and sandbox supervisors; loopback works only when every process
shares the middleware's network namespace. Binding outside loopback is an
explicit opt-in because the development server is unauthenticated and insecure;
restrict port exposure to trusted networks. Then reference `__SERVICE_NAME__` from
a sandbox policy's middleware stage. Review the supervisor middleware documentation
for the policy syntax supported by your pinned OpenShell release.

## Version-matched generated files

- `proto/supervisor_middleware.proto` is the exact downloaded contract.
- `src/__PACKAGE_NAME__/bindings/` contains generated protobuf and gRPC modules.
- `middleware-dev-manifest.json` records the release, source URL, and SHA-256.
- `uv.lock` records the Python dependency solution.

Commit these files. Refresh all version-matched artifacts together with:

```sh
omkit update --openshell-version latest
```

Use a release tag instead of `latest` for a reproducible update.

The starter is deliberately permissive. Before deployment, validate untrusted
configuration, bound request and response work, avoid logging request content,
and return stable deny/error behavior for failures appropriate to your policy.
