import { createHash } from "node:crypto";

const TASK_MARKER = "\n<Task>\n";
const POLICY_START = "<openshell-policy>";
const POLICY_END = "</openshell-policy>";
const ACCEPTANCE_MARKER = "\n## Acceptance Contract\n";

export interface OpenShellChildRequest {
  prompt: string;
  childPolicy: string;
}

export function idempotencyKeyFromPi(input: {
  runId: string;
  stepIndex: number;
  agent: string;
  promptDigest: string;
}): string {
  return createHash("sha256")
    .update(JSON.stringify([input.runId, input.stepIndex, input.agent, input.promptDigest]))
    .digest("hex");
}

export function childRequestFromPrompt(prompt: string): OpenShellChildRequest | undefined {
  // Only unwrap the harness envelope. A literal <Task> inside delegated work
  // must not cause the real task or its launch policy to be discarded.
  const markerIndex = prompt.startsWith("<System instructions>\n")
    ? prompt.indexOf(TASK_MARKER)
    : -1;
  const taskStart = markerIndex >= 0
    ? markerIndex + TASK_MARKER.length
    : prompt.startsWith("<Task>\n")
      ? "<Task>\n".length
      : 0;
  const task = prompt.slice(taskStart);
  const start = task.indexOf(POLICY_START);
  if (start < 0) return undefined;
  const policyStart = start + POLICY_START.length;
  const end = task.indexOf(POLICY_END, policyStart);
  if (end < 0 || task.indexOf(POLICY_START, policyStart) >= 0) return undefined;

  const childPolicy = task.slice(policyStart, end).trim();
  if (!childPolicy) return undefined;

  const absoluteStart = taskStart + start;
  const absoluteEnd = taskStart + end + POLICY_END.length;
  const before = prompt.slice(taskStart, absoluteStart);
  let after = prompt.slice(absoluteEnd);
  if (before.endsWith("\n") && after.startsWith("\n")) after = after.slice(1);
  const taskWithoutPolicy = `${before}${after}`.trim();
  const acceptanceIndex = taskWithoutPolicy.lastIndexOf(ACCEPTANCE_MARKER);
  const childPrompt = acceptanceIndex >= 0
    ? taskWithoutPolicy.slice(0, acceptanceIndex).trim()
    : taskWithoutPolicy;
  if (!childPrompt) return undefined;
  return { prompt: childPrompt, childPolicy };
}
