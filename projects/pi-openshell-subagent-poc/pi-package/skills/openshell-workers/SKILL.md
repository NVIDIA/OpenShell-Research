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
- For public GitHub work, include exactly one full canonical
  `https://github.com/OWNER/REPO` URL in each worker task. Author a child policy
  that grants Git read access only to that repository.
- For two or more independent repositories, launch one worker per repository in
  one asynchronous `workflowScript` using `runs.all`, then combine their
  returned output in the parent.
- Preserve the user's requested scope. Do not add exhaustive analysis, testing,
  dependency installation, acceptance gates, or implementation requirements
  unless the user requested them.
- Do not ask one worker to access multiple repositories.
- The worker may execute arbitrary instructions, but its OpenShell policy still
  limits which files, processes, network endpoints, and credentials it can use.
