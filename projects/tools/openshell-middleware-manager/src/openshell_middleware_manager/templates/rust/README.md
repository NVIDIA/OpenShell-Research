# __PROJECT_NAME__

A Rust OpenShell supervisor middleware starter. It is pinned to the OpenShell
contract recorded in `.openshell-middleware-manifest.json` and starts as a pass-through:
valid pre-credentials HTTP requests continue without mutation.

The service implements OpenShell's HTTP hooks and requires the
`openshell.supervisor-middleware.http-hooks` capability. Use it with a gateway
and sandbox supervisors running OpenShell v0.1.9 or newer.

## Develop

Use Rust 1.90 or newer, then run:

```sh
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test --locked
```

Start the middleware on loopback for local development:

```sh
cargo run --locked -- 127.0.0.1:50051
```

The service implementation is in `src/lib.rs`. Extend `validate_config`,
`evaluate_http_request_preflight`, and `evaluate_http_request_body` with your
policy and request handling. Keep transport adaptation at this boundary and
move substantial domain logic into separate modules.

`evaluate_http_request_preflight` receives the request target, headers, and
policy config. It can continue, deny, or change headers. To inspect or replace
the body, select whole-body inspection when OpenShell offers it:

```rust
let whole_body = pb::HttpBodyMode::WholeBody as i32;
if preflight.permitted_body_modes.contains(&whole_body) {
    let max_body_bytes = preflight
        .limits
        .as_ref()
        .map_or(0, |limits| limits.max_whole_body_bytes);
    return pb::HttpRequestHookResult {
        result: Some(HookResult::PreflightResult(pb::HttpPreflightResult {
            decision: Some(pb::http_preflight_result::Decision::Inspect(
                pb::HttpInspect {
                    mode: Some(pb::http_inspect::Mode::WholeBody(
                        pb::HttpWholeBodyMode { max_body_bytes },
                    )),
                },
            )),
            ..Default::default()
        })),
    };
}
```

OpenShell then sends the complete body to `evaluate_http_request_body`. The starter
does not implement streaming (DUPLEX) inspection; selecting it requires handling
the `input_chunk` and `input_end` events in `evaluate_http_request_event`.

## Connect OpenShell

When the gateway or supervisors use another network namespace, explicitly bind
the development server to a reachable interface:

```sh
cargo run --locked -- 0.0.0.0:50051
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

- `proto/supervisor_middleware.proto` and the OpenShell protocol files it imports are
  the exact downloaded contract.
- `build.rs` generates Rust modules into Cargo's `OUT_DIR` from that contract.
- `.openshell-middleware-manifest.json` records the release and each protocol file's
  source URL and SHA-256.
- `Cargo.lock` records the Rust dependency solution.

Commit these files. Refresh all version-matched artifacts together with:

```sh
omm update --openshell-version latest
```

Use a release tag instead of `latest` for a reproducible update.

The starter is deliberately permissive. Before deployment, validate untrusted
configuration, bound request and response work, avoid logging request content,
and return stable deny/error behavior for failures appropriate to your policy.
