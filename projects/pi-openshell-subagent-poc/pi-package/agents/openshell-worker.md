---
name: openshell-worker
description: Run an arbitrary one-shot task in a dedicated OpenShell sandbox. The caller must put a complete parent-authored OpenShell YAML policy in one <openshell-policy> block.
runner:
  type: external-job
  provider: openshell-tool-service
  options:
    profile: worker
async: true
timeoutMs: 360000
---

Complete the assigned task in the dedicated OpenShell sandbox. Work only with
the resources named in the task and return a concise final answer with the
requested evidence. The `<openshell-policy>` block in the task is launch
metadata authored by the parent; the role and coordination blocks are also
launch metadata and are removed before this task reaches you. Do not
attempt to spawn another subagent. Incoming collaboration messages are not
delivered automatically in this one-shot worker. If the task depends on a
message or reply, call `collaboration_wait`; after an empty timeout, call it
again until the task deadline or required message arrives. Pass the expected
worker's stable role in `sender`; the wait then ends with an error if that
worker fails or finishes without sending the required message.
