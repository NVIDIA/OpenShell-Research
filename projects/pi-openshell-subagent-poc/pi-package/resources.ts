import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const TASK_MARKER = "\n<Task>\n";
const POLICY_START = "<openshell-policy>";
const POLICY_END = "</openshell-policy>";
const ROLE_START = "<openshell-role>";
const ROLE_END = "</openshell-role>";
const COORDINATION_START = "<openshell-coordination>";
const COORDINATION_END = "</openshell-coordination>";
const ACCEPTANCE_MARKER = "\n## Acceptance Contract\n";
const ROLE_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
const RUN_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;

export interface OpenShellChildRequest {
  prompt: string;
  childPolicy: string;
  participantAlias?: string;
  coordination: {
    mode: "immediate" | "all-ready";
    expectedWorkers?: number;
  };
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

export function workflowIdFromPi(
  input: { runId: string },
  options: { tempRoot?: string } = {},
): string | undefined {
  if (!RUN_ID_PATTERN.test(input.runId)) return undefined;
  const configuredRoot = options.tempRoot ?? process.env.PI_SUBAGENTS_TEMP_ROOT?.trim();
  const tempRoot = configuredRoot || join(
    tmpdir(),
    `pi-subagents-uid-${process.getuid?.() ?? "unknown"}`,
  );
  try {
    const raw = readFileSync(
      join(tempRoot, "async-subagent-runs", input.runId, "status.json"),
      "utf8",
    );
    const status = JSON.parse(raw) as { parentWorkflowRunId?: unknown };
    return typeof status.parentWorkflowRunId === "string" &&
      RUN_ID_PATTERN.test(status.parentWorkflowRunId)
      ? status.parentWorkflowRunId
      : undefined;
  } catch {
    return undefined;
  }
}

function coordinationFromTask(task: string): {
  task: string;
  coordination: OpenShellChildRequest["coordination"];
} | undefined {
  const start = task.indexOf(COORDINATION_START);
  if (start < 0) return { task, coordination: { mode: "immediate" } };
  const valueStart = start + COORDINATION_START.length;
  const end = task.indexOf(COORDINATION_END, valueStart);
  if (end < 0 || task.indexOf(COORDINATION_START, valueStart) >= 0) return undefined;
  const fields = new Map<string, string>();
  for (const rawLine of task.slice(valueStart, end).trim().split("\n")) {
    const match = rawLine.trim().match(/^([a-z_]+):\s*(.+)$/);
    if (!match || fields.has(match[1])) return undefined;
    fields.set(match[1], match[2].trim());
  }
  if ([...fields.keys()].some((key) => key !== "mode" && key !== "expected_workers")) {
    return undefined;
  }
  const mode = fields.get("mode");
  if (mode !== "immediate" && mode !== "all-ready") return undefined;
  const expectedRaw = fields.get("expected_workers");
  if (mode === "immediate" && expectedRaw !== undefined) return undefined;
  const expectedWorkers = expectedRaw === undefined ? undefined : Number(expectedRaw);
  if (
    mode === "all-ready" &&
    (!Number.isInteger(expectedWorkers) || expectedWorkers === undefined || expectedWorkers < 2 || expectedWorkers > 64)
  ) return undefined;
  let after = task.slice(end + COORDINATION_END.length);
  if (task.slice(0, start).endsWith("\n") && after.startsWith("\n")) after = after.slice(1);
  return {
    task: `${task.slice(0, start)}${after}`.trim(),
    coordination: {
      mode,
      ...(expectedWorkers !== undefined ? { expectedWorkers } : {}),
    },
  };
}

export function childRequestFromPrompt(prompt: string): OpenShellChildRequest | undefined {
  const markerIndex = prompt.lastIndexOf(TASK_MARKER);
  const taskStart = markerIndex >= 0
    ? markerIndex + TASK_MARKER.length
    : prompt.startsWith("<Task>\n")
      ? "<Task>\n".length
      : 0;
  const task = prompt.slice(taskStart);
  const start = task.indexOf(POLICY_START);
  if (start < 0) {
    return undefined;
  }
  const policyStart = start + POLICY_START.length;
  const end = task.indexOf(POLICY_END, policyStart);
  if (end < 0 || task.indexOf(POLICY_START, policyStart) >= 0) {
    return undefined;
  }
  const policy = task.slice(policyStart, end).trim();
  if (!policy) {
    return undefined;
  }

  const absoluteStart = taskStart + start;
  const absoluteEnd = taskStart + end + POLICY_END.length;
  const before = prompt.slice(taskStart, absoluteStart);
  let after = prompt.slice(absoluteEnd);
  if (before.endsWith("\n") && after.startsWith("\n")) {
    after = after.slice(1);
  }
  let taskWithoutPolicy = `${before}${after}`.trim();
  let participantAlias: string | undefined;
  const roleStart = taskWithoutPolicy.indexOf(ROLE_START);
  if (roleStart >= 0) {
    const roleValueStart = roleStart + ROLE_START.length;
    const roleEnd = taskWithoutPolicy.indexOf(ROLE_END, roleValueStart);
    if (roleEnd < 0 || taskWithoutPolicy.indexOf(ROLE_START, roleValueStart) >= 0) {
      return undefined;
    }
    participantAlias = taskWithoutPolicy.slice(roleValueStart, roleEnd).trim();
    if (!ROLE_PATTERN.test(participantAlias)) return undefined;
    taskWithoutPolicy = `${taskWithoutPolicy.slice(0, roleStart)}${taskWithoutPolicy.slice(
      roleEnd + ROLE_END.length,
    )}`.trim();
  }
  const coordinated = coordinationFromTask(taskWithoutPolicy);
  if (!coordinated) return undefined;
  taskWithoutPolicy = coordinated.task;
  const acceptanceIndex = taskWithoutPolicy.lastIndexOf(ACCEPTANCE_MARKER);
  const childPrompt = acceptanceIndex >= 0
    ? taskWithoutPolicy.slice(0, acceptanceIndex).trim()
    : taskWithoutPolicy;
  return {
    prompt: childPrompt,
    childPolicy: policy,
    ...(participantAlias ? { participantAlias } : {}),
    coordination: coordinated.coordination,
  };
}
