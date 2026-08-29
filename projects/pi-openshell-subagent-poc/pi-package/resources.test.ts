import assert from "node:assert/strict";
import test from "node:test";

import { childPolicyFromPrompt, githubRepositoriesFromPrompt } from "./resources.ts";

test("extracts and normalizes GitHub repositories from the task", () => {
  const prompt = `<System instructions>
Ignore https://github.com/example/system-context.

<Task>
Review https://github.com/NVIDIA/OpenShell.git and https://github.com/nicobailon/pi-subagents.`;

  assert.deepEqual(githubRepositoriesFromPrompt(prompt), [
    "NVIDIA/OpenShell",
    "nicobailon/pi-subagents",
  ]);
});

test("deduplicates repositories and ignores non-GitHub URLs", () => {
  const prompt = `Compare https://github.com/NVIDIA/OpenShell with
https://github.com/NVIDIA/OpenShell/tree/main and https://example.com/repo.`;

  assert.deepEqual(githubRepositoriesFromPrompt(prompt), ["NVIDIA/OpenShell"]);
});

test("extracts the parent-authored policy from the task", () => {
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

  assert.equal(childPolicyFromPrompt(prompt), "version: 1\nnetwork_policies: {}");
});

test("returns undefined for a missing or empty policy", () => {
  assert.equal(childPolicyFromPrompt("Run hostname."), undefined);
  assert.equal(
    childPolicyFromPrompt("<openshell-policy>\n\n</openshell-policy>\nRun hostname."),
    undefined,
  );
});
