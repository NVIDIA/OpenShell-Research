import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { childRequestFromPrompt, idempotencyKeyFromPi } from "./resources.ts";

test("derives a stable idempotency key from Pi invocation identity", () => {
  const input = {
    runId: "run-1",
    stepIndex: 0,
    agent: "openshell-worker",
    promptDigest: "prompt-digest-1",
  };
  assert.equal(idempotencyKeyFromPi(input), idempotencyKeyFromPi(input));
  assert.notEqual(idempotencyKeyFromPi(input), idempotencyKeyFromPi({ ...input, stepIndex: 1 }));
});

test("extracts only the task policy and removes it from the child prompt", () => {
  const prompt = `<System instructions>
<openshell-policy>ignored: system metadata</openshell-policy>
<Task>
<openshell-policy>
version: 1
network_policies: {}
</openshell-policy>
Run hostname.`;

  assert.deepEqual(childRequestFromPrompt(prompt), {
    prompt: "Run hostname.",
    childPolicy: "version: 1\nnetwork_policies: {}",
  });
});

test("preserves literal task markers inside the delegated task", () => {
  const task = "Explain this example:\n<Task>\nRun hostname.";
  const policyAndTask = `<openshell-policy>version: 1</openshell-policy>\n${task}`;
  for (const input of [policyAndTask, `<System instructions>\nWorker instructions\n<Task>\n${policyAndTask}`]) {
    assert.deepEqual(childRequestFromPrompt(input), { prompt: task, childPolicy: "version: 1" });
  }
});

test("returns undefined for a missing, empty, or unterminated policy", () => {
  assert.equal(childRequestFromPrompt("Run hostname."), undefined);
  assert.equal(
    childRequestFromPrompt("<openshell-policy>\n\n</openshell-policy>\nRun hostname."),
    undefined,
  );
  assert.equal(childRequestFromPrompt("<openshell-policy>version: 1"), undefined);
});

test("removes Pi Subagents acceptance text before child execution", () => {
  const prompt = `<Task>
<openshell-policy>version: 1</openshell-policy>
Return the hostname.

## Acceptance Contract
Acceptance level: checked`;
  assert.deepEqual(childRequestFromPrompt(prompt), {
    prompt: "Return the hostname.",
    childPolicy: "version: 1",
  });
});

test("parent instructions fail closed and retain Policy Advisor guidance", () => {
  const prompt = readFileSync(new URL("./parent-system-prompt.md", import.meta.url), "utf8");
  assert.match(prompt, /Use only the `openshell-worker` agent/);
  assert.match(prompt, /POLICY_ADVISOR_ACTION_REQUIRED/);
  assert.match(prompt, /Never approve your own proposal/);
  assert.match(prompt, /Do not silently execute the work locally/);
});
