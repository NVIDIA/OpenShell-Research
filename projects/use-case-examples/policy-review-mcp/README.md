# Task-aware OpenShell policy review MCP demo

This use-case example lowers the cost of checking a capable coding agent's
delegation policy. It provides two independent local stdio MCP servers:

- `policy-review-prover-mcp` runs the external `openshell-prover` exactly once
  to check a complete candidate against an operator-owned boundary.
- `policy-review-jev-mcp` asks TypeSafe JEV one batch of typed questions about
  whether supported permissions fit the exact delegated task and execution
  context.

The caller owns ordering and interpretation. The prover is a containment check,
not a task-fit review. JEV is a fast second opinion, not a policy generator,
formal proof, approval decision, or least-privilege score. Neither server edits
or activates policies, invokes the other service, or spawns an agent.

Implementation is tracked in
[OpenShell-Research issue #78](https://github.com/NVIDIA/OpenShell-Research/issues/78).

## Supported scope

The JEV service inventories the full YAML document but initially assesses only:

- each exact `filesystem_policy.read_only` and `read_write` entry; and
- GitHub REST endpoint groups for `api.github.com` with explicit enforced
  method/path rules and binary selectors.

Process, Landlock, other network families, access presets, deny rules, query
constraints, and selectors whose interactions cannot be represented faithfully
are returned under `coverage.unassessed`. Full YAML input does not imply full
semantic coverage. Unknown nested fields fail closed for their affected
filesystem or network group rather than silently producing a partial
assessment. The external prover has its own modeled coverage and reports that
separately.

## Prerequisites

- Python 3.11 or newer and `uv`.
- TypeSafe access and `TYPESAFE_API_KEY` for the JEV process only. The demo also
  accepts the existing `TYPESAFEAI_API_KEY` alias when the standard name is not
  available.
- The OpenShell prover CLI built from NVIDIA/OpenShell revision
  [`484f0768fc6a0d93e0a2be295c1679aed24e18a9`](https://github.com/NVIDIA/OpenShell/commit/484f0768fc6a0d93e0a2be295c1679aed24e18a9).

At that revision, build the external executable from the OpenShell checkout:

```bash
cargo build --release -p openshell-prover-cli --bin openshell-prover
install -m 0755 target/release/openshell-prover ~/.local/bin/openshell-prover
```

The adapter supports prover JSON `schema_version: 1` and the documented exit
codes: 0 within, 1 exceeds, 2 input/adapter error, 3 unsupported or
inconclusive, and 130 cancelled. Only `within_boundary` with exit code 0 passes.

## Install and configure

```bash
cd projects/use-case-examples/policy-review-mcp
uv sync --extra jev --group dev
cp prover.config.example.toml prover.toml
cp jev.config.example.toml jev.toml
export TYPESAFE_API_KEY=...
```

Relative paths in either TOML file resolve from that config file. Keep the API
key out of TOML and `.env` files committed to source control.

Register the two commands separately in an MCP client. A representative
configuration is:

```json
{
  "mcpServers": {
    "openshell-policy-prover": {
      "command": "uv",
      "args": ["run", "policy-review-prover-mcp", "--config", "/absolute/path/prover.toml"]
    },
    "openshell-delegation-review": {
      "command": "uv",
      "args": ["run", "--extra", "jev", "policy-review-jev-mcp", "--config", "/absolute/path/jev.toml"],
      "env": {"TYPESAFE_API_KEY": "supply-through-your-secret-manager"}
    }
  }
}
```

Do not put `TYPESAFE_API_KEY` in the prover process environment. Both servers
reserve stdout for MCP and use no gateway or shared session.

## Run the ordered demo

The runner is an MCP client, not a third service. It starts the prover first,
skips JEV after every non-pass result, and combines reports only when their
exact UTF-8 candidate fingerprints match.

```bash
uv run --extra jev python demo/run_demo.py read_issue_broad
uv run --extra jev python demo/run_demo.py read_issue_narrow
uv run --extra jev python demo/run_demo.py publish_comment
uv run --extra jev python demo/run_demo.py prepared_checkout_review
uv run --extra jev python demo/run_demo.py outside_boundary
uv run --extra jev python demo/run_demo.py vague_assignment
uv run --extra jev python demo/run_demo.py misleading_rationale
uv run --extra jev python demo/run_demo.py dynamic_write_choice
```

`read_issue_broad` deliberately grants a repository-wide GET selector and issue
comment POST for a return-only summary. The expected demonstration is that the
formal boundary passes while JEV can question task fit. `read_issue_narrow` is a
well-designed policy that should need no follow-up. Expected categories in
`demo/fixtures/scenarios.yaml` are evaluation labels, never substitutes for live
answers.

If a candidate changes, restart at the prover. If only task context changes,
retain the candidate fingerprint but treat the new `review_input_sha256` as a
separate assessment. Do not rephrase a stable task or rubric to seek a favorable
score.

## Tool results

`check_policy_boundary(candidate_policy)` returns the candidate and boundary
SHA-256 values, original v1 prover report, coverage, categorical result,
counterexample or reason, prover version, and elapsed time. Adapter failures are
distinct from proof results.

`review_delegation(...)` validates duplicate keys, sizes, annotations, pointers,
supported shapes, bounded YAML depth/node counts, and the complete request size
before any model call. YAML aliases are rejected. The MCP tool schema exposes
the nested execution-context, annotation, and custom-question fields directly.
It returns:

- candidate and deterministic review-input fingerprints;
- `complete`, `incomplete`, `invalid_input`, or `unavailable` status;
- supported and unassessed coverage;
- task justification, excess scope, and context-gap answers per group;
- a separate write-necessity answer for read/write permissions;
- fixed-category located findings with probabilities and confidence; and
- separately labeled custom-question answers and timings.

`complete` means the declared supported scope was assessed. It is not approval.
Missing context or an answer below the configured confidence, winning
probability, or probability-margin thresholds produces `incomplete` and
suppresses actionable scope guidance. Excess write scope is reported separately
from whether any write access is needed. Custom questions receive each
referenced policy value, source location, and supported/unassessed coverage—not
only its JSON pointer.

## Verification

Run focused checks from this directory:

```bash
uv run --group dev pytest
uv run --group dev ruff check .
```

The tests cover duplicate-key, alias, size, and source-location behavior;
annotation changes; nested coverage; discoverable MCP schemas; single-batch
core/custom assessments; resolved custom-question values; write-scope semantics;
uncertainty handling; adapter contract validation; candidate fingerprints; and
caller ordering. The fake model and fake prover tests do not claim live-service
behavior.

The JEV integration is pinned to `typesafe-sdk==0.7.0`. A live experiment must
be run in an environment where `TYPESAFE_API_KEY` is actually exported; API
availability, latency, model behavior, and evaluation disagreements should be
recorded rather than replaced by fixture expectations.

## Security and limitations

- Policies and task context leave the machine when sent to TypeSafe. Do not send
  secrets, credentials, proprietary code, or sensitive diffs without approval.
- SHA-256 values detect byte mismatches; they do not authenticate intent or
  authorize activation.
- Source locations are parser-derived highlights. A prover counterexample is not
  necessarily a unique YAML line or exhaustive diff.
- The demo reviews supplied policy bytes. It does not prove that a host later
  installs those bytes or that the runtime behaves as expected.
- The implementation does not search policy variants, average answers into an
  approval score, or automatically revise and retry.

See [demo/workflow.md](demo/workflow.md) for the concise process, the
[live JEV evaluation record](demo/live-evaluation.md) for measured behavior,
and the [design plan](../../../plans/jev-policy-mcp-demo.md) for the intended
experience.

## References

- [TypeSafe JEV introduction](https://typesafe.ai/blog/introducing-system-one-models-and-jev)
- [TypeSafe primitives](https://docs.typesafe.ai/introduction)
- [OpenShell policy prover reference at the pinned revision](https://github.com/NVIDIA/OpenShell/blob/484f0768fc6a0d93e0a2be295c1679aed24e18a9/docs/reference/policy-prover.mdx)
- [MCP tools specification](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)
