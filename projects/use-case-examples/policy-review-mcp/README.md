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
- TypeSafe access and `TYPESAFE_API_KEY` for the JEV process only.
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

## Check just the JEV API

With `TYPESAFE_API_KEY` exported in your shell, run from the repository root:

```bash
cd projects/use-case-examples/policy-review-mcp
uv run python demo/check_jev_api.py
```

This sends one small request directly to JEV; it needs no prover, MCP client, or
TOML configuration. It prints the model, choice, confidence, and probabilities.
The expected choice is `no` for a task that only reads a file. A successful
response verifies API access; missing credentials or a failed request produce
a nonzero exit code. If your key is exported in `.bashrc`, run from a Bash
terminal that has loaded it. The request uses your TypeSafe account's API quota
and may incur charges.

## Install and configure

```bash
cd projects/use-case-examples/policy-review-mcp
uv sync --group dev
cp prover.config.example.toml prover.toml
cp jev.config.example.toml jev.toml
export TYPESAFE_API_KEY=...
```

Ensure `openshell-prover` is on `PATH`, or set `executable` in `prover.toml` to
the absolute path of your built binary. An unavailable prover causes the ordered
demo to skip JEV and report `jev.status: not_assessed`.

The TypeSafe SDK is included in the default installation. Relative paths in
either TOML file resolve from that config file. Keep the API
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
      "args": ["run", "policy-review-jev-mcp", "--config", "/absolute/path/jev.toml"],
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
uv run python demo/run_demo.py read_issue_narrow
uv run python demo/run_demo.py read_issue_broad
uv run python demo/run_demo.py read_issue_with_comment
uv run python demo/run_demo.py publish_comment
uv run python demo/run_demo.py prepared_checkout_read_only
uv run python demo/run_demo.py prepared_checkout_review
uv run python demo/run_demo.py outside_boundary
```

These core examples form controlled comparisons:

| Compare | What changes |
| --- | --- |
| `read_issue_narrow` → `read_issue_broad` | Same task/runtime; repository-wide reads and comment-write permission replace exact reads. |
| `read_issue_with_comment` → `publish_comment` | Same policy/runtime; the task now asks to publish, making comment-write permission relevant. |
| `prepared_checkout_read_only` → `prepared_checkout_review` | Same read-only review task/runtime; only checkout write permission is added. |
| `outside_boundary` | Creating an issue exceeds the boundary; JEV is skipped. |

The runtime is deliberately modeled: a preloaded, self-contained `gh` needs
only its executable and TLS trust file, uses environment authentication, and
does not write config, caches, or scratch files. Checkout tools are host-provided
and only read the prepared tree; they do not execute tests. These are review
fixtures, not proof that an arbitrary `gh` installation or actual workload can
run under these policies. The boundary names the exact runtime paths because
the pinned prover cannot resolve sandbox filesystem paths independently.

Then explore ambiguity, annotations, and custom choices:

```bash
uv run python demo/run_demo.py vague_assignment
uv run python demo/run_demo.py misleading_rationale
uv run python demo/run_demo.py dynamic_write_choice
```

`read_issue_broad` deliberately grants a repository-wide GET selector and issue
comment POST for a return-only summary. The expected demonstration is that the
formal boundary passes while JEV can question task fit. `read_issue_narrow` and
`prepared_checkout_read_only` are intended adequate baselines, not guaranteed
model outcomes. `vague_assignment` changes only the task; `misleading_rationale`
changes only an annotation relative to `read_issue_broad`. `dynamic_write_choice`
adds a question to the checkout-write example. Expected categories in
`demo/fixtures/scenarios.yaml` are evaluation labels, never substitutes for live
answers.

### Reading the terminal report

The runner prints a Rich report by default: the exact task, separate prover and
JEV outcomes, a next step, and one compact row per assessed permission group. It also
lists policy fields JEV did not assess. Ordinary service logs are hidden; add
`--verbose` to show them on stderr.

| Display | Meaning |
| --- | --- |
| Prover: WITHIN BOUNDARY | The candidate stays within the configured boundary in the prover's model. This does not establish task fit. |
| Prover: EXCEEDS BOUNDARY | The candidate grants authority outside the boundary; the counterexample shows why. |
| Prover: NOT VERIFIED | The check failed or could not reach a conclusion. |
| JEV: SKIPPED | The prover did not pass, so JEV was not called. |
| JEV: ASSESSED | Supported groups were assessed; this is not an approval. |
| JEV: PARTIAL / UNCERTAIN | Some answers are uncertain, conflicting, or lack context. Read the per-group results; independent findings can still support guidance. |
| JEV: UNAVAILABLE / INVALID INPUT | The API request failed or review input needs correction. |

Use `--details` for the diagnostic panels. For each group, **Task fit** asks whether the permissions are justified by the
assignment. **Excess scope** is an expected score from 0 (fits) through 1 (some
unnecessary access) to 2 (substantial unrelated access). **Context** describes
missing information. **Write needed?** asks whether any write access is needed,
independently of whether the writable path is too broad.

Confidence is the model's certainty, not the probability that a policy is safe.
Selected probability is the probability assigned to the displayed choice; it
is distinct from the SDK's confidence value. `UNCERTAIN` marks an answer that
does not meet the configured confidence/distribution criteria. The report uses
the assessment's existing uncertainty flags without applying new thresholds.

**Consider a change** identifies a finding whose guidance is actionable.
**Investigate** means context, certainty, or agreement is insufficient to recommend
an edit. In the detailed view this is labeled **Needs investigation**.
Matching fingerprints only establish that both reports describe the
same candidate; a mismatch suppresses actionable presentation. Custom answers
are shown separately with the actual question, referenced fields, and leading
option descriptions/probabilities. Near ties are explicitly marked uncertain.

For the full machine-readable report, including every probability and fingerprint:

```bash
uv run python demo/run_demo.py read_issue_broad --details
uv run python demo/run_demo.py read_issue_broad --json
```

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
Missing context, contradictory answers, or an answer below the configured confidence,
winning probability, or probability-margin thresholds produces `incomplete`.
Each finding requires sufficient evidence for its own dimension and a confident
no-context-gap answer. Missing context or contradictions block guidance for the
whole affected group; an uncertain excess score alone does not veto independent,
confident write-necessity evidence. Findings expose `blocked_by` reasons.
Thresholds are unchanged in rubric v2. Excess write scope is reported separately
from whether any write access is needed. Custom questions receive each
referenced policy value, source location, and supported/unassessed coverage—not
only its JSON pointer.

Task-fit findings use `permission_not_justified`: rejecting a permission group
does not establish that every action it grants is unnecessary. Specific excess
scope and unnecessary-write claims require their separate question's evidence.

## Verification

Run focused checks from this directory:

```bash
uv run --group dev pytest
uv run --group dev ruff check .
OPENSHELL_PROVER=/absolute/path/openshell-prover uv run --group dev pytest
```

The tests cover duplicate-key, alias, size, and source-location behavior;
annotation changes; nested coverage; discoverable MCP schemas; single-batch
core/custom assessments; resolved custom-question values; write-scope semantics;
uncertainty handling; adapter contract validation; candidate fingerprints; and
caller ordering. The fake model and fake prover tests do not claim live-service
behavior.

The project CI job runs locked dependencies, Ruff, and credential-free tests.
The six real-prover tests skip unless `OPENSHELL_PROVER` points to the pinned
executable. Live JEV requests are manual, not CI prerequisites.

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
