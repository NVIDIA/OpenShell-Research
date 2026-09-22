# Live JEV evaluation record

Date: 2026-09-21

Model and SDK:

- `jev-1.13.0`
- `typesafe-sdk==0.7.0`

The API key was loaded from the existing interactive Bash environment. The key
value was not printed, persisted, or passed to the prover. Current scripts use
the SDK's standard `TYPESAFE_API_KEY` environment variable.

## Broad issue-summary case

Task: summarize `acme/widget#42` and its discussion, return the summary, and do
not publish it. Candidate: `candidate-broad.yaml`.

The first live request completed in 361 ms of model time. It assessed four
permission groups and returned `incomplete`: three runtime-context findings and
one `resource_scope_too_broad` finding.

A second request supplied explicit claims for the installed `gh` runtime's
reads under `/usr` and `/etc`, temporary writes under `/tmp`, and GitHub HTTPS
reads. It completed in 386 ms of model time. JEV returned:

| Group | Justification | Excess score | Context gap |
| --- | --- | ---: | --- |
| `/usr` read | justified | 1.20 | unknown executable needs |
| `/etc` read | justified | 1.19 | unknown executable needs |
| `/tmp` read/write | justified | 0.60 | unknown output/runtime needs |
| GitHub REST group | unjustified | 1.06 | other |

This is a useful disagreement with the seeded expectations. JEV identified the
mixed broad-read/comment group as unjustified, but continued to abstain because
the supplied runtime claims were not evidence it considered sufficient. The
renderer therefore retains `missing_runtime_context` and the detected
`unneeded_action`, but marks the latter non-actionable. It does not recommend a
policy edit while the context gap remains.

A verification request after that renderer change completed in 436 ms of model
time and returned four non-actionable `missing_runtime_context` findings plus
one non-actionable `unneeded_action` finding for the GitHub REST group.

These requests are feasibility evidence, not threshold calibration. The
remaining scenarios should be evaluated repeatedly before treating any
highlighting threshold as stable.

## Stdio runner check

An earlier check timed out during MCP session initialization. After the review
fixes, a fresh stdio check initialized the JEV server, listed its one tool, and
called `review_delegation` through the Python MCP client. The server sent a live
`jev-1.13.0` request, received HTTP 200 in 283 ms, and returned `complete` for a
single narrow read-only group with no findings. This verifies the JEV MCP path,
including nested request decoding and tool-schema discovery.

## Full ordered demo verification (2026-09-22)

An existing release build was found in the sibling OpenShell checkout, outside
`PATH`. Setting its absolute path in the local `prover.toml` resolved the
`prover_unavailable` error. All 28 project tests passed, including the five real
prover fixture cases.

`uv run python demo/run_demo.py read_issue_broad` then completed both MCP calls
in 1,292 ms. The prover returned `within_boundary`; JEV returned HTTP 200 and
assessed four groups in 359 ms. Its review status was `incomplete` because of
context gaps and uncertainty, with eight non-actionable findings. The candidate
fingerprints matched and the runner returned `combined: true`.

The runner applies a 30-second read timeout and reports a concise `runner_error`
for transport failures.

## Controlled examples and compact reporting (2026-09-22)

The earlier eight-example sweep returned `incomplete` for every case reaching
JEV, with 52 findings and no actionable guidance. At 80 columns, the seven JEV
reports occupied 95–109 lines. Broad, underexplained runtime grants obscured
the intended task comparisons.

The revised fixtures specify a modeled prepared runtime, remove irrelevant
scratch writes, and introduce two adequate-policy comparisons. Rubric v2 asks
about decision-relevant context gaps and includes documented runtime writes.
Confidence/distribution thresholds were **not lowered**. Findings use their own
dimension's evidence; missing context and conflicting answers still block
guidance. A rejected group is now `permission_not_justified`, not an unsupported
claim that its entire action is unnecessary. The default report counts groups,
not overlapping findings, and `--details` preserves diagnostic evidence.

Final validation ran all ten scenarios twice through the actual stdio runner:
20 prover calls and 18 live JEV requests, with no transport or API failures.
The candidate fingerprints matched in all paired reports. Inputs and the rubric
were unchanged between passes; this is a small reproducibility check, not a
calibrated benchmark. Earlier development runs are not included in this table.

| Scenario | Pass 1 / pass 2 JEV status | Observation in both passes | Lines at 80 columns |
| --- | --- | --- | ---: |
| `read_issue_narrow` | complete / complete | No findings; all three groups justified. | 29 |
| `read_issue_broad` | incomplete / complete | GitHub scope not justified; change guidance retained despite varying excess-score certainty. | 29 |
| `read_issue_with_comment` | complete / complete | Comment-capable group not justified for a return-only task. | 32 |
| `publish_comment` | complete / complete | Same policy now fits; no findings. | 31 |
| `prepared_checkout_read_only` | complete / complete | Read-only baseline fits; no findings. | 18 |
| `prepared_checkout_review` | complete / complete | Writes unnecessary; change guidance. | 21 |
| `outside_boundary` | not_assessed / not_assessed | Prover rejected issue creation; JEV was not called. | 11 |
| `vague_assignment` | incomplete / incomplete | Ambiguous network task fit and context; no change guidance. | 28 |
| `misleading_rationale` | incomplete / complete | Rationale did not justify the broader GitHub group. | 29 |
| `dynamic_write_choice` | incomplete / incomplete | Core write finding actionable; custom choice remains uncertain. | 27 |

JEV-path end-to-end latency was 1.21–1.44 seconds. At 120 columns, reports took
10–24 lines. The compact view preserves full selectors by wrapping them rather
than truncating paths; detailed confidence, distributions, blockers, source
locations, and coverage reasons are available with `--details` or `--json`.

Important disagreements remain visible:

- The broad GitHub example does not consistently yield a high enough numeric
  excess score to emit `resource_scope_too_broad`. Its task-fit finding is the
  reproducible signal; do not present the excess score as a stable metric.
- The vague assignment's selected context label was `none`, but its confidence
  was insufficient. It demonstrates uncertainty, not a reliable specific
  `missing_runtime_context` label.
- The custom choice selected read-only with 67% probability versus 32% writable
  in both final passes, but confidence was only 56%, below the unchanged 60%
  threshold. The report explicitly marks it uncertain despite stronger core
  write-necessity evidence.

The examples now illustrate useful contrasts without turning uncertain model
preferences into approval. The modeled runtime assumptions remain essential;
these checks did not launch a real delegated workload under these policies.

## Native OpenShell context and rule-level review (2026-09-22)

Rubric v3 keeps the same Choice/Score dimensions and thresholds. It replaces
flattened permission groups with the complete parsed native candidate policy,
optional native starting policy, exact JSON-pointer targets, model-visible
coverage, and `openshell-review-semantics-v1`. The semantics reference is a
demo-authored summary with pinned OpenShell sources, not a substitute for runtime
validation. Each REST allow rule now has its own assessment with the complete
enclosing network policy available as context.

All ten examples were run twice again through the ordered stdio runner. Both
passes completed with 20 real-prover calls and 18 live JEV requests; every
paired report had matching candidate fingerprints. The rubric, fixtures, and
thresholds were unchanged between these two passes.

| Scenario | JEV status in both passes | Observed guidance in both passes |
| --- | --- | --- |
| `read_issue_narrow` | complete | Both filesystem entries and both GET rules fit; no findings. |
| `read_issue_broad` | incomplete | Specifically flags `rules/1`, the POST comment rule; wildcard GET remains uncertain. |
| `read_issue_with_comment` | complete | Specifically flags `rules/2`, the POST comment rule; neither sibling GET rule is flagged. |
| `publish_comment` | complete | The same three allow rules fit the publishing task; no findings. |
| `prepared_checkout_read_only` | complete | Read-only checkout fits; no findings. |
| `prepared_checkout_review` | incomplete | `/filesystem_policy/read_write/0` has unnecessary writes; excess-score uncertainty remains. |
| `outside_boundary` | not_assessed | Prover rejects issue creation; JEV is skipped. |
| `vague_assignment` | incomplete | Network intent remains unresolved; no change guidance. |
| `misleading_rationale` | incomplete | Specifically flags the POST comment rule despite the annotation. |
| `dynamic_write_choice` | incomplete | Core checkout-write guidance remains; uncertainty is retained. |

JEV-path end-to-end latency was 1.22–1.36 seconds. The default reports took
11–38 lines at 80 columns and 10–28 at 120 columns. Detailed reports were also
rendered from the same returned JSON, including exact rule locations and
enclosing-context pointers.

This validates improved finding precision, not universally improved model
accuracy: wildcard-read excess remains an unresolved assessment, and the
checkout excess score remains uncertain. We did not lower thresholds or change
the fixtures to force those answers. The reports assess authored task fit, not
the effective runtime behavior of a launched workload.
