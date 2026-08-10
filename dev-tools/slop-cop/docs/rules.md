# Rules

Rules detect editorial signals. Repository configuration determines whether a
signal is advisory, chargeable, or blocking and how it affects the score.

## Manage declarative rules

Use a declarative rule for a literal phrase or bounded regular expression. Add
the rule block to `slop-cop.toml` and add positive and counterexample cases to
`tests/rule_cases.toml`. No engine or renderer change is required.

Each rule has a stable ID, positive integer version, category, title, rationale,
advice, severity, allowance, and explicit scoring values. Increment the version
when matching behavior or external-response interpretation changes.

To disable a rule, set `enabled = false`. To remove one, remove its definition,
cases, exceptions, and source suppressions in the same change. Run
`slop-cop validate-rules` to find dangling references and missing coverage.

Declarative regular expressions are limited to 500 characters. They may use the
configured `IGNORECASE`, `MULTILINE`, `DOTALL`, and `VERBOSE` flags. Inline
flags, backreferences, recursive constructs, conditionals, and lookbehind are
rejected. Matching has a bounded timeout.

## Add custom Python logic

Use a custom Python rule when detection requires an algorithm or an external
service. Create one module under `src/slop_cop/rules/custom/`, export one rule
instance, and add it to `CUSTOM_RULES` in that directory's `__init__.py`.

```python
from slop_cop.rules.api import (
    FunctionRule,
    RuleContext,
    RuleEvaluation,
    RuleMetadata,
    RuleRuntime,
    RuleSignal,
)

METADATA = RuleMetadata(
    id="custom.repeated-claim-opener",
    version=1,
    category="repetition",
    title="Repeated claim opener",
    rationale="Repeated claim openings make consecutive paragraphs formulaic.",
    advice="Vary the paragraph structure or combine the claims.",
)


async def evaluate(context: RuleContext, runtime: RuleRuntime) -> RuleEvaluation:
    signals = tuple(
        RuleSignal(start=item.start, end=item.end, key=item.normalized)
        for item in context.repeated_sentence_starts(minimum_count=3)
    )
    return RuleEvaluation(signals=signals)


RULE = FunctionRule(metadata=METADATA, evaluator=evaluate)
```

Register it explicitly:

```python
from .repeated_claim_opener import RULE as REPEATED_CLAIM_OPENER

CUSTOM_RULES = (REPEATED_CLAIM_OPENER,)
```

Add the scoring policy separately:

```toml
[rules."custom.repeated-claim-opener"]
enabled = true
severity = "warning"
max_signal_units = 1
fixed_allowance = 0
first_cost = 3
repeat_cost = 2
cap = 9
on_error = "fail"
```

The engine supplies Markdown projection, source mapping, segmentation,
suppressions, scoring, and reporting. The custom evaluator only returns bounded
signals and optional audit data. It cannot set the score, threshold, decision,
or override state.

Add positive and counterexample entries to `tests/rule_cases.toml`. The shared
contract suite validates IDs, metadata, result bounds, spans, ordering, and
fixture coverage for built-in, declarative, and custom rules.

## Call an external judge

An external custom rule declares a named service in its metadata and obtains it
through `RuleRuntime`:

```python
response = await runtime.service("editorial_judge").post_json(
    {"schema_version": 1, "prose": context.projected_prose}
)
result = JudgeResponse.model_validate(response.data)
signal = RuleSignal.document(
    key=result.label,
    units=result.strength,
    detail=result.explanation,
    evidence=context.map_exact_quotes(result.evidence),
)
return RuleEvaluation(
    signals=(signal,),
    audit={**response.audit, "judge_revision": result.judge_revision},
)
```

Configure the service in trusted repository configuration:

```toml
[services.editorial_judge]
url = "https://judge.internal.example/v1/evaluate"
token_env = "SLOP_COP_EDITORIAL_JUDGE_TOKEN"
timeout_seconds = 20
max_response_bytes = 65536
max_attempts = 1
required_judge_revision = "editorial-v1"
```

The custom rule owns its request data, strict response model, and conversion to
signals. The runtime owns the allowed origin, authentication, deadlines,
redirect rejection, response limit, idempotency key, and content-safe audit
record. A required CI rule must use this runtime instead of direct sockets,
subprocesses, SDK transports, or an unconfigured destination.

Document-scoped signals do not participate in passage-density calculations.
Emit exact source spans when local concentration matters. Evidence quotations
are display evidence and do not add scoring units.

Use `on_error = "fail"` for an external rule that affects the required CI
threshold. Use `advisory` only for a zero-point experiment. Tests must use a fake
transport and cover clean, chargeable, malformed, oversized, wrong-revision,
timeout, and transport outcomes.
