# Scoring

Slop Cop starts each file at 100 and subtracts bounded category costs. The
default Dev Notes threshold is 80. A score describes configured editorial
signals; it is not a probability, authorship judgment, or factuality grade.

## Rule cost

Rules emit signals. After suppressions and overlap deduplication, Slop Cop sums
their bounded units and applies the rule allowance:

```text
document_excess = max(0, units - allowance(document))

base_cost = 0                                           when excess is 0
base_cost = first_cost + repeat_cost * (excess - 1)    otherwise
```

An allowance may combine a fixed count with a document-density allowance based
on the rule's natural opportunity: words, sentences, or paragraphs. A phrase
rule and a sentence-opening rule therefore do not share an arbitrary universal
denominator.

## Passage density

Selected rules and categories also inspect rolling word, sentence, or paragraph
windows. Slop Cop finds the single densest window and charges a bounded density
cost from its peak excess:

```text
peak_excess = max(0, signals_in_window - allowed_units)

density_cost = 0
    when peak_excess is 0
else min(density_cap,
         density_first_cost + density_repeat_cost * (peak_excess - 1))
```

Only primary, unsuppressed, exact source spans enter these windows. The peak is
charged once even when overlapping windows contain the same findings. Appending
clean prose cannot lower an existing peak. Document-scoped external judgments
do not enter passage-density calculations.

The final rule cost is capped:

```text
rule_cost = min(rule_cap, base_cost + density_cost)
```

This permits two occurrences spread across a long note while still charging a
cluster in three neighboring paragraphs when that rule's policy says
concentration matters.

## Category and file decisions

Related rules share a category cap. An optional category-density cost can
capture a cluster of several distinct weak signals:

```text
category_cost = min(category_cap,
                    sum(rule_costs) + category_density_cost)
score = max(0, round(100 - sum(category_costs)))
```

A file passes only when its score meets the threshold, it has no unsuppressed
blocking finding, and required analysis completed. An advisory external error
marks the analysis incomplete without manufacturing a penalty. A required
external error fails the analysis.

Each changed Dev Note is scored independently. The PR score is the lowest file
score, and every changed file must pass. A clean second file cannot conceal a
failing first file. Base scores and finding changes appear for comparison, but
only the head result controls enforcement.

## Tune policy

Tune allowances, costs, density windows, and caps in `slop-cop.toml`; do not put
score calculations inside detector code. Every scored change needs fixtures at
the zero, first, repeat, density, and cap boundaries. Confirm that:

- a common isolated construction does not fail a clean note;
- a concentrated passage costs more than the same findings spread apart;
- clean appended prose does not dilute an existing density cost;
- overlapping detectors do not double charge one source span;
- one weak category cannot exceed its cap;
- the accepted Dev Note remains at or above 80 without a blanket exception;
- dense multi-category fixtures remain below 80.

Keep unresolved or context-sensitive rules advisory with zero points until
their counterexamples and score boundaries are reliable.

## Calibration benchmarks

`benchmarks/dev-note-history.toml` records immutable Git revisions and focused
repository fixtures with expected decisions and acceptable score ranges.
`slop-cop benchmark` scores each source with the active configuration and fails
when a score or decision leaves its declared range. This makes calibration drift
a tested policy change instead of an incidental result of editing rule weights.

The current references follow one Dev Note through revision and publication:

| Reference | Expected score | Baseline score |
| --- | ---: | ---: |
| [2026-07-13 initial draft](https://github.com/NVIDIA/OpenShell-Research/blob/2b8ae6ef4b74a1eeafa767f9d9f0238af17bdcd5/docs/dev-notes/posts/2026-07-13-policy-controlling-reachy-mini-with-openshell.md) | 70–79, fail | 76 |
| [2026-07-14 revised draft](https://github.com/NVIDIA/OpenShell-Research/blob/808336e88d418e36364d57d6fda87e47e21dba82/docs/dev-notes/posts/2026-07-13-policy-controlling-reachy-mini-with-openshell.md) | 80–89, pass | 86 |
| [2026-07-16 revised draft](https://github.com/NVIDIA/OpenShell-Research/blob/95a17f58c65df19166d92e70346cb7574ec84871/docs/dev-notes/posts/2026-07-13-policy-controlling-reachy-mini-with-openshell.md) | 84–91, pass | 88 |
| [2026-07-17 revised draft](https://github.com/NVIDIA/OpenShell-Research/blob/be515c9c2684e1e3febec058f1a9e0e90da16a72/docs/dev-notes/posts/2026-07-13-policy-controlling-reachy-mini-with-openshell.md) | 88–94, pass | 91 |
| [2026-07-18 revised draft](https://github.com/NVIDIA/OpenShell-Research/blob/069696b0a28ffd0cd77e1e043a532f81b9cec3e2/docs/dev-notes/posts/2026-07-13-policy-controlling-reachy-mini-with-openshell.md) | 94–100, pass | 97 |
| [2026-07-20 published draft](https://github.com/NVIDIA/OpenShell-Research/blob/b0d481796b8a0492053c7b3cac0c65444a2e99be/docs/dev-notes/posts/2026-07-20-policy-controlling-reachy-mini-with-openshell.md) | 92–98, pass | 95 |
| Clean technical fixture | 100, pass | 100 |
| Legitimate contrast and citation fixture | 95–99, pass | 97 |
| Normal technical structure fixture | 100, pass | 100 |
| Dense multi-family fixture | 0–79, fail | 72 |
| Blocking assistant residue fixture | 0, fail | 0 |

Historical revisions detect drift against real prose; their chronology is not a
quality label. Focused fixtures define the intended treatment of clean prose,
legitimate constructions, dense independent signals, and blocking residue. Use
ranges wide enough for targeted detector improvements but narrow enough to catch
a material scoring regression. Update a range only when the new result is an
intentional editorial-policy change supported by the source.
