# __PROJECT_NAME__

A Rust OpenShell supervisor middleware starter. It is pinned to the OpenShell
contract recorded in `middleware-dev-manifest.json` and starts as a pass-through:
valid pre-credentials HTTP requests are allowed without mutation.

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

The service implementation is in `src/lib.rs`. Extend `validate_config` and
`evaluate_http_request` with your policy and request handling. Keep transport
adaptation at this boundary and move substantial domain logic into separate
modules.

## Connect OpenShell

When the gateway or supervisors use another network namespace, explicitly bind
the development server to a reachable interface:

```sh
cargo run --locked -- 0.0.0.0:50051
```

Register the running service in the gateway configuration:

```toml
[[openshell.supervisor.middleware]]
name = "__SERVICE_NAME__"
grpc_endpoint = "http://<supervisor-reachable-host>:50051"
allow_insecure = true
max_body_bytes = 4194304
timeout = "500ms"
```

Replace `<supervisor-reachable-host>` with a host IP or DNS name reachable from
both the gateway and sandbox supervisors; loopback works only when every process
shares the middleware's network namespace. Binding outside loopback is an
explicit opt-in because the development server is unauthenticated and insecure;
restrict port exposure to trusted networks. `allow_insecure = true` is required
for this plaintext development endpoint and is only appropriate on trusted local
or isolated networks. Production and shared deployments should use an
authenticated TLS endpoint instead. Then reference `__SERVICE_NAME__` from a
sandbox policy's middleware stage. Review the supervisor middleware
documentation for the policy syntax supported by your pinned OpenShell release.

## Version-matched generated files

- `proto/supervisor_middleware.proto` is the exact downloaded contract.
- `build.rs` generates Rust modules into Cargo's `OUT_DIR` from that contract.
- `middleware-dev-manifest.json` records the release, source URL, and SHA-256.
- `Cargo.lock` records the Rust dependency solution.

Commit these files. When changing the OpenShell version, regenerate the project
or deliberately update the contract and manifest together.

The starter is deliberately permissive. Before deployment, validate untrusted
configuration, bound request and response work, avoid logging request content,
and return stable deny/error behavior for failures appropriate to your policy.
