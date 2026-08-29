const TASK_MARKER = "\n<Task>\n";
const POLICY_START = "<openshell-policy>";
const POLICY_END = "</openshell-policy>";
const GITHUB_URL =
  /https:\/\/github\.com\/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))\/([A-Za-z0-9_.-]{1,100})(?=[/#?\s),.;:]|$)/g;

export function githubRepositoriesFromPrompt(prompt: string): string[] {
  const markerIndex = prompt.lastIndexOf(TASK_MARKER);
  const task = markerIndex >= 0 ? prompt.slice(markerIndex + TASK_MARKER.length) : prompt;
  const repositories = new Set<string>();

  for (const match of task.matchAll(GITHUB_URL)) {
    const repository = match[2].replace(/[.,;:]+$/, "").replace(/\.git$/, "");
    repositories.add(`${match[1]}/${repository}`);
  }
  return [...repositories];
}

export function childPolicyFromPrompt(prompt: string): string | undefined {
  const markerIndex = prompt.lastIndexOf(TASK_MARKER);
  const task = markerIndex >= 0 ? prompt.slice(markerIndex + TASK_MARKER.length) : prompt;
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
  return policy || undefined;
}
