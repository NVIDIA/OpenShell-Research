# OpenShell subagent routing

This Pi session is the parent agent in the OpenShell subagent POC.

Whenever you delegate work to any subagent:

1. Use only the `openshell-worker` agent. Never use Pi's built-in `worker`,
   `reviewer`, `scout`, or other in-process agents.
2. Read and follow both of these installed skills before launching the worker:
   - `/opt/pi-openshell-poc/skills/openshell-workers/SKILL.md`
   - `/opt/pi-openshell-poc/skills/generate-sandbox-policy/SKILL.md`
3. Generate a separate least-privilege OpenShell policy for each worker's exact
   task and include it in that worker's `<openshell-policy>` block.
4. For independent tasks, launch the `openshell-worker` jobs in parallel with
   one `workflowScript` and `runs.all`.
5. Do not perform delegated repository cloning or review work in the parent
   sandbox. The dedicated OpenShell child must perform it.
6. Preserve the user's requested scope in each child task. Do not turn a
   concise repository review into an exhaustive security audit, implementation
   task, or full test run. Do not add acceptance, gate, or agent-contract
   overrides to the workflow item.

For a repository review, unless the user asks for more, tell the child only to
clone the assigned repository, report its HEAD commit, inspect its README and
primary source structure, and return a concise summary of its purpose,
architecture, strengths, and notable risks. Do not ask it to install
dependencies or run tests unless the user explicitly requests that work.

If you cannot construct the required child policy or launch
`openshell-worker`, report the failure. Do not silently fall back to a built-in
subagent or execute the delegated work in the parent.
