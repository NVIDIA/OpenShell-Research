---
title: Add an adjudicator
description: Decide policy proposals on the trusted host while the harness applies decisions and fails closed.
agent_markdown: true
---

# Add an adjudicator

When the agent asks OpenShell for more capability, the gateway holds the
request as a pending proposal. The harness fetches it and asks the adjudicator
to approve or reject. The adjudicator only decides; the harness applies the
decision through the SDK and fails closed when the gateway will not accept an
approval.

Adjudicators live in `adjudicators/`. Three ship today:

| Adjudicator | Behavior | Use |
| --- | --- | --- |
| `auto-approve` | Approves everything. | Measures what enforcement alone stops; lets `hello-canary` reach its objective. |
| `reject-all` | Rejects everything. | The control condition: the policy never changes. |
| `model-reviewer` | A separate model with no tools decides against an invariant supplied by the scenario. | The AI reviewer in `github-policy-review`. |

## The contract

```ts
export interface Adjudicator {
  name: string
  decide(proposal: Proposal, context: AdjudicationContext): Promise<Decision>
}

export type AdjudicatorFactory = (options: { runDir: string; instanceFacts: Record<string, unknown> }) => Adjudicator

export interface Decision {
  decision: 'approve' | 'reject'
  reason: string
}
```

An adjudicator is built once per run by its factory, which receives the run
directory and the scenario's instance facts. That is where per-run state such
as a decision history or a model client belongs.

`Proposal` is the gateway's draft-policy chunk: the rule name, the agent's
rationale and security notes, the proposed rule, the current and candidate
effective policies with their hashes, the validation result, and the review
token needed to apply a decision. `AdjudicationContext` adds the run directory,
the instance facts, the 1-based decision number, the current effective policy,
and the milliseconds remaining before the adjudicator must answer.

Throwing from `decide` is safe. The harness records the failure, rejects the
proposal with the error message, counts an `adjudicator_failure`, and continues.
A run with adjudicator failures and no reached objective is invalid.

## What the harness does with a decision

| `application` in `decisions.jsonl` | Meaning |
| --- | --- |
| `applied` | The decision took effect as returned. |
| `approval_failed_then_rejected` | The gateway refused the approval during validation, so the harness rejected the proposal instead. Counted as a fail-closed approval. |
| `review_stale_retry` | The proposal changed since it was fetched. The harness refetches and asks again. |
| `rejection_already_satisfied` | The proposal was already rejected, for example by the gateway. |
| `failed` | Neither the decision nor the fallback rejection could be applied. Counted as a `decision_apply_failure`. |

Two cases never reach the adjudicator. A proposal the gateway has already
marked with an application error is rejected immediately and recorded with
`preflight: true`. A proposal still pending when the run's settle grace period
ends is rejected with an explicit reason and recorded with `runEnded: true`.

After an applied approval, the harness refetches the pending list rather than
deciding the remaining proposals it already holds, because the effective policy
changed and their review tokens are stale.

## Walkthrough: model-reviewer

`adjudicators/model-reviewer.ts` is about 140 lines.

- The factory reads `reviewerInstructions` and `protectedRepository` from the
  instance facts, so the invariant is owned by the scenario, not the
  adjudicator. It fails fast when a scenario does not supply instructions.
- The model endpoint, name, reasoning level, and key come from the
  `LAB_REVIEWER_*` variables, each falling back to the agent model's setting.
- For each proposal it writes the exact packet it will show the model as
  `reviewer-input-NNN.json`: the protected resource, the proposal metadata,
  the proposed rule, and the full candidate effective policy. It requests a
  strict JSON schema response of `{decision, reason}`.
- It keeps a bounded history of its own prior decisions (16 messages or
  240,000 characters), never replaying superseded policy snapshots. On a
  context-length error it clears the history once and retries.
- Transient HTTP errors are retried with capped exponential backoff until the
  context's remaining time is spent. Every attempt's raw response is saved.

## Register it

```ts
export const adjudicators: Record<string, AdjudicatorFactory> = {
  'auto-approve': autoApprove,
  'reject-all': rejectAll,
  'model-reviewer': modelReviewer,
  'mine': myAdjudicator,
}
```

Select it with `--adjudicator mine`, or make it a scenario's default in
`scenario.json`.
