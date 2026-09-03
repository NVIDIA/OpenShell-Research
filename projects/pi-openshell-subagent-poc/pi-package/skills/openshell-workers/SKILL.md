---
name: openshell-workers
description: Delegate isolated tasks to OpenShell child sandboxes and require a generated child policy for every openshell-worker launch.
---

# OpenShell workers

Use the `openshell-worker` subagent when work should run in a fresh OpenShell
sandbox.

- Give each worker a complete, self-contained task.
- Before every worker launch, read and follow
  `../generate-sandbox-policy/SKILL.md`. Use it to author one complete policy
  for that worker's exact task. This step is required even when the task needs
  no ordinary network access.
- Put the generated raw YAML at the start of the worker task between exactly
  one `<openshell-policy>` and `</openshell-policy>` block. Do not wrap it in a
  Markdown code fence. The Tool Service applies the policy exactly as written.
- Give every worker a stable, unique role name such as `repo-a-reviewer`. Put
  it in exactly one `<openshell-role>` and `</openshell-role>` block immediately
  after the policy. Use that role name when other participants address it.
- Choose the workflow start mode from the tasks:
  - Independent workers need no coordination block. Each starts as soon as its
    own sandbox is ready, reports to the parent, and exits.
  - When any worker must exchange messages with a sibling, put the same block
    in every worker task immediately after its role:
    `<openshell-coordination>`, then `mode: all-ready`, then
    `expected_workers: N`, then `</openshell-coordination>`. `N` is the exact
    number of entries in that `runs.all` call. This holds every child Pi process
    until all declared sandboxes are ready.
- For GitHub work, include the full canonical repository URL in the worker task
  and author the narrowest policy that permits the task's required Git
  operations. Do not silently reduce a requested write task to read-only.
- For two or more independent repositories, launch one worker per repository in
  one asynchronous `workflowScript` using `runs.all`, then combine their
  returned output in the parent.
- Preserve the user's requested scope. Do not add exhaustive analysis, testing,
  dependency installation, acceptance gates, or implementation requirements
  unless the user requested them.
- Prefer separate workers for independent repositories when the work can run in
  parallel; do not impose that split when one task genuinely spans repositories.
- The worker may execute arbitrary instructions, but its OpenShell policy still
  limits which files, processes, network endpoints, and credentials it can use.
- Messages to the parent are stored durably and visible in the collaboration
  watcher. Ordinary messages, progress, and results do not interrupt the parent;
  final worker answers return through Pi Subagents. Use `kind: question` when the
  parent must respond, or set payload `actionRequired: true` for another message
  that genuinely requires immediate action. Those actionable messages trigger a
  parent Pi turn. One-shot workers receive messages only through
  `collaboration_wait`. When a worker's task depends on a message or reply, tell
  it to call `collaboration_wait` with the expected sender's stable role and
  repeat after an empty timeout until the task deadline. The wait fails
  immediately if that worker has failed or finished without sending the needed
  message. The parent and workers can use
  `collaboration_list_participants` and `collaboration_send`. A worker addresses
  its parent as `parent` and siblings by their stable role names.

## Parent-policy approval

If the Tool Service returns `policy-review-denied` with
`POLICY_ADVISOR_ACTION_REQUIRED`, no child sandbox was created. For a
network-only increase:

- Read `/etc/openshell/skills/policy-advisor/SKILL.md` in the parent sandbox.
- Convert only the missing network authority from the already-authored child
  policy into narrow Policy Advisor `addRule` operations.
- Submit them through `http://policy.local/v1/proposals` and wait for the human
  decision. Never approve them yourself.
- After all required chunks report `approved` and `policy_reloaded: true`,
  launch a new `openshell-worker` request. Do not reattach to the failed job.
- On rejection or an unsupported non-network increase, report the result and
  stop unless the human provides narrowing guidance.
