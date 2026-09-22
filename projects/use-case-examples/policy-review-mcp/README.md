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
- each `network_policies.<name>.endpoints[n].rules[m]` REST allow rule for
  `api.github.com`, with its enclosing endpoint and binary restrictions.

Process, Landlock, other network families, access presets, deny rules, query
constraints, and selectors whose interactions cannot be represented faithfully
are returned under `coverage.unassessed`. Full YAML input does not imply full
semantic coverage. Unknown nested fields fail closed for their affected
filesystem section or network endpoint rather than silently producing a partial
assessment. The external prover has its own modeled coverage and reports that
separately.

## What JEV receives

The model receives the complete **parsed native OpenShell candidate policy**,
not a replacement permission schema. Field names, nested objects, arrays,
authored values, and omitted fields are preserved; YAML comments/formatting are
not sent. An optional starting policy is passed in the same native structure.
It is comparison context, not the operator boundary.

Each core question explicitly names a JSON pointer into `candidate_policy`.
For example, `/network_policies/github/endpoints/0/rules/2` identifies one allow
rule, not the entire endpoint. The full configuration remains available so JEV
can interpret that rule with its endpoint, binaries, and sibling rules.
Filesystem questions identify individual `read_only[n]` or `read_write[n]`
entries. "Review target" means only the location being assessed; it is not a
new OpenShell permission type.

The application-defined request envelope also includes:

| Field | Purpose |
| --- | --- |
| `candidate_policy`, `starting_policy` | Native parsed configuration; starting policy is null when omitted. |
| `review_targets` | Candidate JSON pointers and pointers to their enclosing context. |
| `delegated_task`, `execution_context` | Exact task and caller-supplied runtime facts. |
| `field_annotations`, `custom_question_context` | Caller rationale, verified annotated changes, and resolved custom references. |
| `coverage` | Supported target pointers, unsupported fields with reasons, and the policy inventory. |
| `policy_semantics` | Versioned, source-linked summary of relevant OpenShell behavior. |
| `operation_examples`, `review_rules` | Demo-authored GitHub examples and assessment instructions—not an official OpenShell schema. |

The [semantics reference](src/policy_review_mcp/reference/openshell-semantics.yaml)
is a **demo-authored summary** grounded in the same pinned OpenShell revision as
the prover. It covers filesystem grants and workdir defaults, binary/ancestor
matching, endpoint enforcement, allow/deny interactions, GET/HEAD matching,
path matching, and runtime limitations. Every section identifies its source.
It also records a schema-prose versus Rego discrepancy in path-glob delimiter
descriptions rather than pretending this demo can prove wildcard containment.

Visible context is not assessed scope: unsupported controls remain in the
native document and coverage report but do not receive core questions. Caller
rationales do not override the assignment. The model does not receive the
operator boundary or prover report and does not prove effective runtime access.
Removing a flagged rule may leave equivalent access through another rule.

All core and custom questions still use one batched JEV request. Question types
remain `Choice` and `Score`; the discussed `Noul` alternative is not implemented.

### Agent-supplied diagnostic choices

A calling agent can add a diagnostic `Choice` for an exact assessed policy entry.
It supplies multiple plausible explanations, including a no-problem alternative;
JEV independently selects among them in the **same API call** as the core rubric.
For example, add this item to `questions`:

```json
{
  "id": "comment_diagnostic",
  "pointers": ["/network_policies/github/endpoints/0/rules/2"],
  "instructions": "Which explanation best fits this comment POST rule?",
  "criteria": {
    "not_requested": "The task requests a returned summary, not a published comment.",
    "wrong_destination": "Publishing is needed, but this rule targets the wrong issue.",
    "justified": "Publishing to this exact issue is part of the delegated task."
  },
  "diagnostic_outcomes": {
    "not_requested": "unjustified",
    "wrong_destination": "unjustified",
    "justified": "justified"
  }
}
```

`diagnostic_outcomes` opts into the diagnostic column and explicitly maps each
caller option to its task-fit conclusion. It must cover all supplied criteria
and include both `justified` and `unjustified` conclusions. The server always
adds **`none_fit`** (none of the alternatives fit) and **`insufficient_context`**;
callers cannot redefine or map these fallback options. One diagnostic per exact
supported target is allowed; ancestor pointers and unsupported fields are
rejected before calling JEV. Ordinary custom questions omit this mapping and
remain separately displayed, with their existing multi-pointer support.

The selected option is a **diagnostic hypothesis**, not a faithful causal
explanation of JEV's core answer or an instruction to change the policy. Option
text and outcome mappings are caller-authored, not independently verified facts.
Use specific, neutral alternatives grounded in the task and runtime, not several
paraphrases of "change the policy." The mapping is used locally to compare
conclusions; the option descriptions themselves are sent to JEV as Choice criteria.

Diagnostic answers expose `diagnostic_outcomes` and `diagnostic_status` alongside
the selected label, descriptions, confidence, distribution, and uncertainty:

| Status | Meaning |
| --- | --- |
| `aligned` | Confident diagnostic and core task-fit conclusions agree. |
| `conflict` | Confident conclusions disagree; the review is incomplete and guidance for that entry is blocked by `diagnostic_conflict`. |
| `uncertain` | The diagnostic does not meet the configured confidence/distribution thresholds. |
| `core_uncertain` | The diagnostic is confident, but core task fit/context is unresolved or internally conflicting. |
| `none_fit`, `insufficient_context` | JEV selected a server-owned fallback; no caller explanation is endorsed. |

An uncertain or fallback diagnostic keeps the review incomplete without erasing
independently supported core findings. A diagnostic never creates an actionable
finding on its own. Agreement is only with core **task fit**; it does not certify
every detail of an explanation or agreement with every other rubric dimension.

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
uv sync --locked --group dev
cp prover.config.example.toml prover.toml
cp jev.config.example.toml jev.toml
export TYPESAFE_API_KEY=...
```

Ensure `openshell-prover` is on `PATH`, or set `executable` in `prover.toml` to
the absolute path of your built binary. An unavailable prover causes the ordered
demo to skip JEV and report `jev.status: not_assessed`.

The TypeSafe SDK is included in the default installation. The boundary path
resolves relative to its TOML file; use an absolute prover executable path or a
command on `PATH`. Keep the API
key out of TOML and `.env` files committed to source control.

Register the two commands separately in an MCP client. A representative
configuration is:

```json
{
  "mcpServers": {
    "openshell-policy-prover": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/policy-review-mcp", "--locked", "policy-review-prover-mcp", "--config", "/absolute/path/to/policy-review-mcp/prover.toml"]
    },
    "openshell-delegation-review": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/policy-review-mcp", "--locked", "policy-review-jev-mcp", "--config", "/absolute/path/to/policy-review-mcp/jev.toml"],
      "env": {"TYPESAFE_API_KEY": "supply-through-your-secret-manager"}
    }
  }
}
```

Replace the absolute paths with your checkout's project directory and TOML files.
`--directory` makes launch independent of the client's working directory; an
absolute `--config` alone does not locate the installed Python package. If a GUI
client cannot find `uv`, use its absolute executable path as `command`.
The JSON shows a common `mcpServers` format; adapt it to your client's configuration
format and secret injection mechanism. GUI clients need not load your `.bashrc`.

Do not put `TYPESAFE_API_KEY` in the prover process environment, including through
inherited client environment variables. Both servers
reserve stdout for MCP and use no gateway or shared session.

At initialization, both servers publish operating instructions. `tools/list`
exposes field descriptions, nested request contracts, typed output schemas, and
read-only tool annotations. The JEV tool explicitly advertises its external
TypeSafe API interaction (`openWorldHint: true`); read-only means no policy
mutation, not no network traffic or API charges. The prover uses only the local
executable (`openWorldHint: false`). These hints are metadata, not a security boundary.

For a first call, send complete YAML **text**, not a path or diff, to
`check_policy_boundary(candidate_policy)`. If `status` is `complete` and
`within_boundary` is true, call `review_delegation` with the same candidate bytes,
the exact delegated `task`, and `execution_context` (`{}` if genuinely unknown).
Annotations, starting policy, and custom questions are optional. Compare the
returned `candidate_sha256` values before interpreting the reports together.
Neither server enforces this cross-server ordering for the caller.

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
adds diagnostic alternatives for unnecessary writes, overly broad writes, or
justified writes to the checkout-write example. `read_issue_with_comment` and
`publish_comment` share identical diagnostic choices, so their comparison still
changes only the task. Expected categories in
`demo/fixtures/scenarios.yaml` are evaluation labels, never substitutes for live
answers.

### Reading the terminal report

The runner prints a Rich report by default: the exact task, separate prover and
JEV outcomes, a next step, and one compact row per assessed filesystem entry or
network allow rule. It also
lists policy fields JEV did not assess. Ordinary service logs are hidden; add
`--verbose` to show them on stderr.

| Display | Meaning |
| --- | --- |
| Prover: WITHIN BOUNDARY | The candidate stays within the configured boundary in the prover's model. This does not establish task fit. |
| Prover: EXCEEDS BOUNDARY | The candidate grants authority outside the boundary; the counterexample shows why. |
| Prover: NOT VERIFIED | The check failed or could not reach a conclusion. |
| JEV: SKIPPED | The prover did not pass, so JEV was not called. |
| JEV: ASSESSED | Supported policy entries were assessed; this is not an approval. |
| JEV: PARTIAL / UNCERTAIN | Some answers are uncertain, conflicting, or lack context. Read the per-target results; independent findings can still support guidance. |
| JEV: UNAVAILABLE / INVALID INPUT | The API request failed or review input needs correction. |

Use `--details` for the diagnostic panels, exact target pointers, source lines,
and enclosing-context pointers. For each entry, **Task fit** asks whether its permissions are justified by the
assignment. **Excess scope** is an expected score from 0 (fits) through 1 (some
unnecessary access) to 2 (substantial unrelated access). **Context** describes
missing information. **Write needed?** asks whether any write access is needed,
independently of whether the writable path is too broad.

Confidence is the model's certainty, not the probability that a policy is safe.
Selected probability is the probability assigned to the displayed choice; it
is distinct from the SDK's confidence value. `UNCERTAIN` marks an answer that
does not meet the configured confidence/distribution criteria. The report uses
the assessment's existing uncertainty flags without applying new thresholds.

The default table has **Policy entry** and **Assessment** columns. **Change
supported** marks actionable core evidence; **Investigate** means evidence is
insufficient to recommend an edit. Detailed findings retain **Consider a change**
and **Needs investigation** labels.

When diagnostic questions are supplied, a **Diagnostic** column shows the selected
option's description beside its exact entry; other rows show a dash. Uncertain
preferences, unresolved core evidence, and conflicts are explicitly labeled.
Fallback selections say that no alternative fits or more context is needed.
The report does not substitute a generic "consider a change" follow-up. Use
`dynamic_write_choice` to try it, or compare `read_issue_with_comment` with
`publish_comment` to see the same alternatives evaluated against different tasks.

Matching fingerprints only establish that both reports describe the
same candidate; a mismatch suppresses actionable presentation. Ordinary custom
answers are shown separately with the question, references, and leading option
descriptions/probabilities. Diagnostic choices get that full evidence view with
`--details`, without duplicating the compact column. Near ties remain uncertain.

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

Both tools return reports in MCP `structuredContent`, validated against their
advertised `outputSchema`, with equivalent JSON text for legacy clients. Nested
JEV assessments, findings, coverage, and custom answers are typed; the external
prover's original JSON and provider-owned coverage/counterexample remain opaque
payloads. Existing report field names and schema versions are retained; absent
optional fields may now be explicit nulls.

Always inspect the report's `status`, not only MCP `isError`. A boundary rejection,
adapter error, invalid review input, or unavailable JEV API can be a successfully
delivered report (`isError: false`). Malformed MCP arguments can instead produce
an MCP tool error without a report. `complete` is never an approval flag.

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
- task justification, excess scope, and context-gap answers per target;
- a separate write-necessity answer for read/write permissions;
- fixed-category located findings with probabilities and confidence; and
- separately labeled custom-question answers and timings.

`complete` means the declared supported scope was assessed. It is not approval.
Missing context, contradictory answers, or an answer below the configured confidence,
winning probability, or probability-margin thresholds produces `incomplete`.
Each finding requires sufficient evidence for its own dimension and a confident
no-context-gap answer. Missing context or contradictions block guidance for the
whole affected target; an uncertain excess score alone does not veto independent,
confident write-necessity evidence. Findings expose `blocked_by` reasons.
Thresholds are unchanged in rubric v3. Excess write scope is reported separately
from whether any write access is needed. Custom questions receive each
referenced policy value, source location, and supported/unassessed coverage—not
only its JSON pointer.

Task-fit findings use `permission_not_justified`: rejecting a policy entry
does not establish that every capability it grants is unnecessary. Specific excess
scope and unnecessary-write claims require their separate question's evidence.

JEV report **schema version 2** replaces `coverage.supported_groups` with
`coverage.supported_targets` (JSON pointers) and `group_id` with
`target_pointer` on assessments/findings. Assessments also include
`context_pointers`; reports identify `semantics_version` and `rubric_version`.
The prover report stays at schema version 1. The finer rule-level assessment
uses three questions per allow rule rather than per endpoint; the existing
question-count limit is checked before any API request, without silent truncation.

## Verification

Run focused checks from this directory:

```bash
uv run --locked --group dev pytest
uv run --locked --group dev ruff check .
OPENSHELL_PROVER=/absolute/path/openshell-prover uv run --locked --group dev pytest
```

The tests cover duplicate-key, alias, size, and source-location behavior;
annotation changes; nested coverage; discoverable MCP schemas; single-batch
core/custom assessments; resolved custom-question values; write-scope semantics;
uncertainty handling; adapter contract validation; candidate fingerprints; and
caller ordering; native candidate/starting-policy preservation; exact rule
locations; enclosing context; and rule-level question limits. The fake model and fake prover tests do not claim live-service
behavior.

The project CI job runs locked dependencies, Ruff, and credential-free tests.
Stdio tests launch both installed entrypoints from an unrelated working directory,
initialize MCP, discover metadata/schemas, and verify structured calls using a
fake local prover and JEV inputs that never contact TypeSafe.
The six real-prover tests skip unless `OPENSHELL_PROVER` points to the pinned
executable. Live JEV requests are manual, not CI prerequisites.

The JEV integration is pinned to `typesafe-sdk==0.7.0`. A live experiment must
be run in an environment where `TYPESAFE_API_KEY` is actually exported; API
availability, latency, model behavior, and evaluation disagreements should be
recorded rather than replaced by fixture expectations.

## Security and limitations

- Complete parsed candidate/starting policies and task context leave the machine
  when sent to TypeSafe, including fields outside the assessed subset. Do not send
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
