# Privacy Guard

Privacy Guard is OpenShell middleware that scans request bodies and applies
policy to detected sensitive data.

## Development commands

Run commands from `projects/privacy-guard/`.

- List targets: `make help`
- Run all checks: `make check`
- Check Python 3.11: `make check-py311`
- Run focused tests: `make test PYTEST_ARGS=tests/test_processor.py`
- Run the benchmark: `make benchmark`

Run focused tests while working and `make check` before handoff. Benchmarks are
diagnostic and have no pass/fail threshold.

## Engineering approach

- Do not preserve backward compatibility unless the user explicitly requests
  it. Update callers, tests, examples, and docs with the change.
- Add defensive handling only for a concrete failure mode at the layer that
  owns it. Avoid speculative guards, duplicate validation, broad catches,
  retries, and fallbacks.

## Project map

- `src/privacy_guard/scanners/`: scanner contract, budgets, and built-ins
- `src/privacy_guard/request_body/`: normalization and reconstruction contracts
- `src/privacy_guard/processor.py`: request orchestration and policy
- `src/privacy_guard/service/`: gRPC lifecycle and protobuf adapter
- `src/privacy_guard/bindings/`: generated protobuf files; never hand-edit
- [`docs/architecture/`](docs/architecture/index.md): symlink to the canonical
  site sources under `../../docs/documentation/privacy-guard/architecture/`
- `tests/`: tests that mirror source boundaries
- `tests/test_hardening.py`: cross-cutting security tests
- `examples/`: copyable deployments

Keep each example's policy, configuration, commands, names, and tests in sync.
Before changing `processor.py`, `scanners/`, `request_body/`, or `service/`,
read the architecture overview and the matching topic page.
Architecture documentation changes follow
[`docs/development/index.md`](../../docs/development/index.md) and require its
checks.

## Design boundaries

- Keep scanners separate from `RequestProcessor` and `service/`. A scanner
  inspects one text block and returns block-relative findings. Do not make
  scanners depend on request-body formats, policy, gRPC, or generated bindings.
- `RequestProcessor` orchestrates format handlers and scanners, applies policy,
  attaches text-block paths, and enforces aggregate limits.
- Outside generated `bindings/`, only `service/` may import gRPC or generated
  bindings.
- Public scanner and format-handler methods validate extension output.
  `RequestProcessor` validates finding identities and offsets. Do not repeat
  these checks elsewhere.
- `ScannerConfig.entity_types` lists every entity the scanner can emit; it is
  independent of policy. Apply policy after scanning.
- Scanner and format-handler instances serve concurrent requests. Do not store
  request content or mutable per-request state on them.
- Format handlers define path syntax. Treat paths as opaque everywhere else.

## Extension pattern

Define a concrete config type and implement `_scan`. Do not override `scan`.

```python
from pydantic import Field

from privacy_guard.scanners import Finding, ScanBudget, Scanner, ScannerConfig


class KeywordScannerConfig(ScannerConfig):
    keyword: str = Field(min_length=1)


class KeywordScanner(Scanner[KeywordScannerConfig]):
    def _scan(self, text_block: str, budget: ScanBudget) -> tuple[Finding, ...]:
        start = text_block.find(self.config.keyword)
        if start < 0:
            return ()
        return (
            Finding(
                entity="keyword",
                scanner_name=self.scanner_name,
                start_offset=start,
                end_offset=start + len(self.config.keyword),
            ),
        )
```

Scanners that need custom initialization logic should implement `_initialize`
instead of `__init__`. The base constructor calls it last, after setting
validated configuration and scanner metadata.

## Change limits

- Add or update tests at the layer that owns the behavior.
- Ask before adding dependencies or changing the protobuf contract, stable error
  codes, protocol limits, or fail-closed defaults.
- Do not remove or weaken tests to pass checks.
- Do not add casts, explicit `Any`, blanket ignores, or broad type suppressions
  to handwritten code. `tests/test_typing_policy.py` enforces this.
