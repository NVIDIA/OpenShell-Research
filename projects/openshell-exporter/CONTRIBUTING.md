# Contributing

From the project root, install the pinned tools and run:

```sh
mise trust
mise install
mise run pre-commit
mise run build
mise run build-proxy
./scripts/build-images.sh all
```

Use `go test ./path/to/package` for focused checks and `mise run test-race` for
concurrency changes. `mise run ci` runs the broader local suite. Collector
integration tests use `TEST_EXPORTER_BINARY` and `TEST_OTLP_PROXY_BINARY` set to
the absolute built-binary paths. External tests require their own services and
credentials; report unexecuted checks as such.

Follow [AGENTS.md](AGENTS.md), keep pins and runnable examples aligned, and avoid
unrelated features or documentation. Submit a PR with the change and test results.
Every commit requires matching author/DCO sign-off:

```sh
git commit -s -m "docs: simplify research example"
```

Report bugs with versions, reproduction steps, and redacted logs through the
[issue tracker](https://github.com/NVIDIA/OpenShell-Research/issues).
For vulnerabilities, follow [SECURITY.md](SECURITY.md).
