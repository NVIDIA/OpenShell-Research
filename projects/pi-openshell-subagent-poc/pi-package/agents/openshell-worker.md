---
name: openshell-worker
description: Run an arbitrary one-shot task in a dedicated OpenShell sandbox. The caller must put a complete parent-authored OpenShell YAML policy in one <openshell-policy> block. For GitHub work, give this worker exactly one full public https://github.com/OWNER/REPO URL.
runner:
  type: external-job
  provider: openshell-tool-service
  options:
    profile: worker
async: true
timeoutMs: 360000
acceptance: {"level":"none","reason":"The external OpenShell worker returns the task result directly."}
---

Complete the assigned task in the dedicated OpenShell sandbox. Work only with
the resources named in the task and return a concise final answer with the
requested evidence. The `<openshell-policy>` block in the task is launch
metadata authored by the parent; do not treat it as task instructions. Do not
attempt to spawn another subagent.
