---
name: openshell-workers
description: Delegate isolated tasks to one-shot Pi workers running in dedicated OpenShell child sandboxes.
---

# OpenShell workers

Use the `openshell-worker` subagent whenever work should run in a fresh
OpenShell sandbox.

- Give each worker a complete, self-contained task.
- Before every launch, follow `../generate-sandbox-policy/SKILL.md` and author
  one complete least-privilege policy for that exact task.
- Put the raw YAML at the beginning of the task between exactly one
  `<openshell-policy>` and `</openshell-policy>` block. Do not use a Markdown
  fence. The Tool Service removes this block before running the child.
- For independent work, launch one worker per task with `runs.all` and combine
  the returned results in the parent.
- Preserve the user's requested scope. Do not add testing, installation,
  implementation, or exhaustive review requirements unless requested.
- The child may execute arbitrary task instructions, but OpenShell enforces its
  filesystem, process, network, and credential policy.

## Parent-policy approval

If the Tool Service returns `policy-review-denied` with
`POLICY_ADVISOR_ACTION_REQUIRED`, no child sandbox was created. For a
network-only increase:

- Read `/etc/openshell/skills/policy-advisor/SKILL.md` in the parent sandbox.
- Submit only the missing network authority as narrow `addRule` operations.
- Wait for human approval and `policy_reloaded: true`.
- Launch a new `openshell-worker`; do not reattach to the denied job.
- Never approve the proposal yourself.
