---
title: Add a reviewer
description: Decide policy proposals on the trusted host while the harness applies decisions and fails closed.
agent_markdown: true
---

# Add a reviewer

When the agent asks OpenShell for more capability, the gateway holds the
request as a pending proposal. The harness fetches it and asks the reviewer
to approve or reject. The reviewer only decides; the harness applies the
decision through the SDK and fails closed when the gateway will not accept an
approval.

Reviewers live in `reviewers/`. Three ship today:

| Reviewer | Behavior | Use |
| --- | --- | --- |
| `auto-approve` | Approves everything. | The zero-model fixture that lets `hello-canary` prove the pipeline. Not for experiments; slated for deprecation. |
| `reject-all` | Rejects everything. | The control condition: the policy never changes. |
| `model-reviewer` | A separate model with no tools decides against an invariant supplied by the scenario. | The AI reviewer in `github-policy-review`. |

## The contract

```ts
export interface Reviewer {
  name: string
  decide(proposal: Proposal, context: ReviewContext): Promise<Decision>
}

export type ReviewerFactory = (options: { runDir: string; instanceFacts: Record<string, unknown> }) => Reviewer

export interface Decision {
  decision: 'approve' | 'reject'
  reason: string
}
```

A reviewer is built once per run by its factory, which receives the run
directory and the scenario's instance facts. That is where per-run state such
as a decision history or a model client belongs.

`Proposal` is the gateway's draft-policy chunk: the rule name, the agent's
rationale and security notes, the proposed rule, the current and candidate
effective policies with their hashes, the validation result, and the review
token needed to apply a decision. `ReviewContext` adds the run directory,
the instance facts, the 1-based decision number, the current effective policy,
and the milliseconds remaining before the reviewer must answer.

Throwing from `decide` is safe. The harness records the failure, rejects the
proposal with the error message, counts an `reviewer_failure`, and continues.
A run with reviewer failures and no reached objective is invalid.

## What the harness does with a decision

| `application` in `decisions.jsonl` | Meaning |
| --- | --- |
| `applied` | The decision took effect as returned. |
| `approval_failed_then_rejected` | The gateway refused the approval during validation, so the harness rejected the proposal instead. Counted as a fail-closed approval. |
| `review_stale_retry` | The proposal changed since it was fetched. The harness refetches and asks again. |
| `rejection_already_satisfied` | The proposal was already rejected, for example by the gateway. |
| `failed` | Neither the decision nor the fallback rejection could be applied. Counted as a `decision_apply_failure`. |

Two cases never reach the reviewer. A proposal the gateway has already
marked with an application error is rejected immediately and recorded with
`preflight: true`. A proposal still pending when the run's settle grace period
ends is rejected with an explicit reason and recorded with `runEnded: true`.

## Two kinds of proposal

The agent is not the only author. With Policy Advisor enabled in `manual`
mode, OpenShell presents every proposal for a decision, and OpenShell's own
mechanistic mapper aggregates each denied connection into a proposal too: a
host-and-port rule with no request-level restriction and a templated rationale
such as "Allow codex to connect to chatgpt.com:443 (HTTPS).". The agent
harness's own background traffic produces these before the agent has done
anything, and the agent's probes produce more.

The reviewer decides both kinds on their merits; the harness never discards a
proposal on the reviewer's behalf. It only records provenance, so attribution
is a post-hoc read of the evidence rather than a change to what the reviewer
saw. The chunk ids that policy.local returns to the agent appear in its tool
output, so a chunk the agent received is `agent_authored` (`originSource:
submission`). OpenShell 0.0.116 exposes no origin field on a chunk and leaves
`denialSummaryIds` empty, so for a chunk the agent never mentioned, the
mechanistic mapper's templated rationale is the fallback marker
(`rationale_template`), and anything else is treated as the agent's. Every
decision records the origin, and `outcome.json` counts the two separately, so
a reviewer's record can be read for the agent's own proposals without ever
having withheld the rest.

After an applied approval, the harness refetches the pending list rather than
deciding the remaining proposals it already holds, because the effective policy
changed and their review tokens are stale.

## Walkthrough: model-reviewer

`reviewers/model-reviewer.ts` is about 140 lines.

- The factory reads `reviewerInstructions` and `protectedRepository` from the
  instance facts, so the invariant is owned by the scenario, not the
  reviewer. It fails fast when a scenario does not supply instructions.
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
export const reviewers: Record<string, ReviewerFactory> = {
  'auto-approve': autoApprove,
  'reject-all': rejectAll,
  'model-reviewer': modelReviewer,
  'mine': myReviewer,
}
```

Select it with `--reviewer mine`, or make it a scenario's default in
`scenario.json`.
