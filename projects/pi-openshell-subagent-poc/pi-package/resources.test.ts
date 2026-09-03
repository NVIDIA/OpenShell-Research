import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  childRequestFromPrompt,
  idempotencyKeyFromPi,
  workflowIdFromPi,
} from "./resources.ts";

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

test("resolves sibling runs to Pi's shared parent workflow", (context) => {
  const tempRoot = mkdtempSync(join(tmpdir(), "openshell-pi-workflow-"));
  context.after(() => rmSync(tempRoot, { recursive: true, force: true }));
  const runDirectory = join(tempRoot, "async-subagent-runs", "child-run-1");
  mkdirSync(runDirectory, { recursive: true });
  writeFileSync(
    join(runDirectory, "status.json"),
    JSON.stringify({ parentWorkflowRunId: "shared-workflow-1" }),
  );

  assert.equal(
    workflowIdFromPi({ runId: "child-run-1" }, { tempRoot }),
    "shared-workflow-1",
  );
  assert.equal(workflowIdFromPi({ runId: "standalone-run" }, { tempRoot }), undefined);
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
    coordination: { mode: "immediate" },
  });
});

test("extracts a stable collaboration role and removes it from the child prompt", () => {
  const prompt = `External runner wrapper
<Task>
<openshell-policy>
version: 1
network_policies: {}
</openshell-policy>
<openshell-role>reviewer-a</openshell-role>
Review the parser.`;

  assert.deepEqual(childRequestFromPrompt(prompt), {
    prompt: "Review the parser.",
    childPolicy: "version: 1\nnetwork_policies: {}",
    participantAlias: "reviewer-a",
    coordination: { mode: "immediate" },
  });
});

test("extracts an explicit all-ready workflow contract", () => {
  const prompt = `<Task>
<openshell-policy>
version: 1
network_policies: {}
</openshell-policy>
<openshell-role>worker-a</openshell-role>
<openshell-coordination>
mode: all-ready
expected_workers: 3
</openshell-coordination>
Send a message to worker-b.`;

  assert.deepEqual(childRequestFromPrompt(prompt), {
    prompt: "Send a message to worker-b.",
    childPolicy: "version: 1\nnetwork_policies: {}",
    participantAlias: "worker-a",
    coordination: { mode: "all-ready", expectedWorkers: 3 },
  });
});

test("rejects malformed coordination metadata", () => {
  const base = `<openshell-policy>version: 1</openshell-policy>`;
  assert.equal(
    childRequestFromPrompt(`${base}<openshell-coordination>mode: all-ready</openshell-coordination>Run.`),
    undefined,
  );
  assert.equal(
    childRequestFromPrompt(`${base}<openshell-coordination>mode: all-ready\nexpected_workers: 65</openshell-coordination>Run.`),
    undefined,
  );
});

test("returns undefined for a missing or empty policy", () => {
  assert.equal(childRequestFromPrompt("Run hostname."), undefined);
  assert.equal(
    childRequestFromPrompt("<openshell-policy>\n\n</openshell-policy>\nRun hostname."),
    undefined,
  );
});

test("removes Pi Subagents inferred acceptance text from an external-job prompt", () => {
  const prompt = `External runner wrapper
<Task>
<openshell-policy>
version: 1
network_policies: {}
</openshell-policy>
Send HELLO_FROM_A to the sibling.

## Acceptance Contract
Acceptance level: checked
Completion is not accepted from prose alone.`;

  assert.deepEqual(childRequestFromPrompt(prompt), {
    prompt: "Send HELLO_FROM_A to the sibling.",
    childPolicy: "version: 1\nnetwork_policies: {}",
    coordination: { mode: "immediate" },
  });
});

test("parent instructions route denied network authority through Policy Advisor", () => {
  const prompt = readFileSync(new URL("./parent-system-prompt.md", import.meta.url), "utf8");

  assert.match(prompt, /POLICY_ADVISOR_ACTION_REQUIRED/);
  assert.match(prompt, /\/etc\/openshell\/skills\/policy-advisor\/SKILL\.md/);
  assert.match(prompt, /http:\/\/policy\.local\/v1\/proposals/);
  assert.match(prompt, /Never approve your own proposal/);
  assert.match(prompt, /launch one new `openshell-worker` request/);
});
