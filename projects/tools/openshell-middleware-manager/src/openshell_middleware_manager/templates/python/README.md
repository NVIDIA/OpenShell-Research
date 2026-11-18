# __PROJECT_NAME__

A Python OpenShell supervisor middleware starter. It is pinned to the OpenShell
contract recorded in `.openshell-middleware-manifest.json` and starts as a pass-through:
valid pre-credentials HTTP requests continue without mutation.

The service implements OpenShell's HTTP hooks and requires the
`openshell.supervisor-middleware.http-hooks` capability. Use it with a gateway
and sandbox supervisors running OpenShell v0.1.9 or newer.

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
`validate_config`, `evaluate_http_request_preflight`, and
`evaluate_http_request_body` with your policy and request handling. Keep
transport adaptation at this boundary and move substantial domain logic into
separate modules.

`evaluate_http_request_preflight` receives the request target, headers, and
policy config. It can continue, deny, or change headers. To inspect or replace
the body, select whole-body inspection when OpenShell offers it:

```python
if pb2.HTTP_BODY_MODE_WHOLE_BODY in preflight.permitted_body_modes:
    return pb2.HttpRequestHookResult(
        preflight_result=pb2.HttpPreflightResult(
            inspect=pb2.HttpInspect(
                whole_body=pb2.HttpWholeBodyMode(
                    max_body_bytes=preflight.limits.max_whole_body_bytes
                )
            )
        )
    )
```

OpenShell then sends the complete body to `evaluate_http_request_body`. The starter
does not implement streaming (DUPLEX) inspection; selecting it requires handling
the `input_chunk` and `input_end` events in `evaluate_http_request_event`.

## Connect OpenShell

When the gateway or supervisors use another network namespace, explicitly bind
the development server to a reachable interface:

```sh
uv run __DISTRIBUTION_NAME__ --listen 0.0.0.0:50051
```

Start the service before the gateway, then register it in the gateway
configuration and restart the gateway:

```toml
[[openshell.supervisor.middleware]]
name = "__SERVICE_NAME__"
grpc_endpoint = "http://<supervisor-reachable-host>:50051"
allow_insecure_transport = true
max_payload_bytes = 4194304
timeout = "500ms"
```

Replace `<supervisor-reachable-host>` with a host IP or DNS name reachable from
both the gateway and sandbox supervisors; loopback works only when every process
shares the middleware's network namespace. Binding outside loopback is an
explicit opt-in because the development server is unauthenticated and insecure;
restrict port exposure to trusted networks. `allow_insecure_transport` lets a
gateway that signs extension tokens call this plaintext endpoint without one.

Attach the registered service to destination hosts in a sandbox policy:

```yaml
network_middlewares:
  __SERVICE_NAME__:
    middleware: __SERVICE_NAME__
    endpoints:
      include:
        - api.example.com
```

OpenShell blocks matching requests while the service is unavailable, because
HTTP hook middleware always fails closed. Review the supervisor middleware
documentation for the policy syntax supported by your pinned OpenShell release.

## Version-matched generated files

- `proto/supervisor_middleware.proto` is the exact downloaded contract.
- `src/__PACKAGE_NAME__/bindings/` contains generated protobuf and gRPC modules.
- `.openshell-middleware-manifest.json` records the release, source URL, and SHA-256.
- `uv.lock` records the Python dependency solution.

Commit these files. Refresh all version-matched artifacts together with:

```sh
omm update --openshell-version latest
```

Use a release tag instead of `latest` for a reproducible update.

The starter is deliberately permissive. Before deployment, validate untrusted
configuration, bound request and response work, avoid logging request content,
and return stable deny/error behavior for failures appropriate to your policy.
