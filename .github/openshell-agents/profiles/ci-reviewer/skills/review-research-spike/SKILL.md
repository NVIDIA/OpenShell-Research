---
name: review-research-spike
description: Review exploratory experiments for valid methods, defensible evidence, reproducibility, and appropriately small implementations rather than production readiness.
---

# Review a research spike

Identify the question being investigated, the method, and what the result
actually establishes. Trace critical calculations, comparisons, data selection,
and reported measurements. Check whether claims follow from the experiment and
whether confounders or limitations would materially change their interpretation.
Negative, null, and inconclusive results are valid outcomes; do not reward only
positive results or require a particular performance improvement.

Assess whether another researcher can reproduce the relevant result from the
documented environment, inputs, commands, and expected outputs. Seeds matter
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

1. `method_validity`: the method can answer the stated question without material
   errors or uncontrolled confounders;
2. `evidence_claims`: reported results support the claims and uncertainty;
3. `reproducibility`: environment, inputs, commands, and outputs permit a
   credible repeat of the relevant experiment;
4. `clarity_limitations`: the question, approach, conclusions, and limits are clear;
5. `implementation_proportionality`: code and verification are sufficient for
   the experiment without unnecessary engineering machinery.

Apply the common score anchors to experimental usefulness, not product maturity.
