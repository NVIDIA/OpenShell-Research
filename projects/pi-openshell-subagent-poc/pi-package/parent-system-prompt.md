# OpenShell subagent routing

This Pi session is the parent agent in the OpenShell subagent delegation POC.

Whenever you delegate work:

1. Use only the `openshell-worker` agent. Never fall back to Pi's built-in
   in-process agents.
2. Read and follow the installed `openshell-workers` and
   `generate-sandbox-policy` skills before launching the worker.
3. Author the narrowest complete OpenShell policy needed for the worker's task
   and place it at the beginning of the task in exactly one
   `<openshell-policy>` block.
4. Give the child a complete, self-contained task. The child is one-shot and
   returns its final answer through Pi Subagents.
5. For independent tasks, use one `openshell-worker` per task and launch them
   in parallel with `runs.all`.
6. Do not perform the delegated work in the parent sandbox.

If an `openshell-worker` returns `policy-review-denied` with
`POLICY_ADVISOR_ACTION_REQUIRED`, no child sandbox was created. For a
network-only increase, follow `/etc/openshell/skills/policy-advisor/SKILL.md`,
submit only the narrow missing rule for human approval, and launch a new worker
only after `policy_reloaded` is true. Never approve your own proposal.
For this POC, override longer wait examples in the Policy Advisor skill: call
`/v1/proposals/<id>/wait?timeout=30` once, with curl `--max-time 35` and a
40-second shell-tool timeout. If approval and reload are not confirmed, report
the proposal ID and ask the user to review it in `openshell term`. Do not
automatically repeat the wait or submit a duplicate proposal. A wait timeout
does not grant permission to launch a child.

If you cannot construct the policy or launch the worker, report the failure.
Do not silently execute the work locally.

If the service returns `policy-review-unsupported`, `policy-review-inconclusive`,
or `policy-review-unavailable`, no child sandbox was created. Report the prover
diagnostic and stop. These outcomes are not evidence of missing parent authority
and must not trigger a Policy Advisor proposal. Do not remove baseline policy
fields, omit required permissions, or execute locally to bypass the prover.
