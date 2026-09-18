# Pi admission middleware

This OMM-managed Rust service exposes authenticated admission HTTPS on port
5443 and the OpenShell `v0.0.116` pre-credentials middleware contract over TLS
on port 50051. Run it from the parent example with `./demo.sh serve`.

OMM owns `.openshell-middleware-manifest.json`,
`proto/supervisor_middleware.proto`, and `Cargo.lock`. Refresh those together:

```sh
omm update --openshell-version v0.0.116
```

Validate handwritten code with:

```sh
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test --locked
```
