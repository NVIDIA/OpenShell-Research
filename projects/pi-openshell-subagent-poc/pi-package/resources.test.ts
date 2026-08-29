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
  assert.notEqual(
    idempotencyKeyFromPi(input),
    idempotencyKeyFromPi({ ...input, stepIndex: 1 }),
  );
  assert.notEqual(
    idempotencyKeyFromPi(input),
    idempotencyKeyFromPi({ ...input, promptDigest: "prompt-digest-2" }),
  );
});

test("extracts the policy and removes it from the child prompt", () => {
  const prompt = `<System instructions>
<openshell-policy>
network_policies:
  github:
    name: repo-read
</openshell-policy>

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

test("returns undefined for a missing or empty policy", () => {
  assert.equal(childRequestFromPrompt("Run hostname."), undefined);
  assert.equal(
    childRequestFromPrompt("<openshell-policy>\n\n</openshell-policy>\nRun hostname."),
    undefined,
  );
});

test("parent instructions route denied network authority through Policy Advisor", () => {
  const prompt = readFileSync(new URL("./parent-system-prompt.md", import.meta.url), "utf8");

  assert.match(prompt, /POLICY_ADVISOR_ACTION_REQUIRED/);
  assert.match(prompt, /\/etc\/openshell\/skills\/policy-advisor\/SKILL\.md/);
  assert.match(prompt, /http:\/\/policy\.local\/v1\/proposals/);
  assert.match(prompt, /Never approve your own proposal/);
  assert.match(prompt, /launch one new `openshell-worker` request/);
});
