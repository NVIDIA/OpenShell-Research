---
name: review-research-spike
description: Review exploratory experiments for valid methods, defensible evidence, reproducibility, and appropriately small implementations rather than production readiness.
---

# Review a research spike

Identify the question being investigated, the documented method, and what the
reported result claims to establish. Read the complete research documentation,
then sample the principal experiment entry point, recorded evidence, and any
critical calculation needed to determine whether the overview is credible. Do
not trace every calculation or implementation path. Check whether the stated
method could support the claims and whether material confounders or limitations
are disclosed.
Negative, null, and inconclusive results are valid outcomes; do not reward only
positive results or require a particular performance improvement.

Assess from the documented environment, inputs, commands, expected outputs, and
representative artifacts whether another researcher has a credible reproduction
path. Seeds matter
when randomness affects the conclusion; baselines matter when a comparative
claim depends on them. Do not mechanically demand either from every experiment.
For hardware, GPU, paid services, or restricted data, disclose what was not run
and assess available fixtures, recorded evidence, and reproduction instructions.
Do not equate a cheap smoke test with replication of the scientific result.

Keep the implementation proportional to the question. Do not demand production
packaging, stable APIs, compatibility layers, generalized infrastructure,
exhaustive validation, or broad test coverage unrelated to experimental validity.
Do flag avoidable complexity that obscures the method or undermines reproduction.

## Rubric

Use these five criteria, in order:

1. `method_validity`: the documented method and sampled implementation can answer
   the stated question without an evident material flaw or undisclosed confounder;
2. `evidence_claims`: representative recorded results support the documented
   claims and uncertainty;
3. `reproducibility`: environment, inputs, commands, and outputs permit a
   credible repeat of the relevant experiment;
4. `clarity_limitations`: the question, approach, conclusions, and limits are clear;
5. `implementation_proportionality`: the project structure and sampled code and
   verification appear sufficient without unnecessary engineering machinery.

Apply the common score anchors to experimental usefulness, not product maturity.
