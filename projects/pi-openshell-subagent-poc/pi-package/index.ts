import {
  ExternalJobProviderError,
  registerExternalJobProvider,
  type ExternalJobHandle,
  type ExternalJobResult,
  type ExternalJobStartInput,
} from "pi-subagents/external-job-provider";
import {
  childRequestFromPrompt,
  idempotencyKeyFromPi,
  workflowIdFromPi,
} from "./resources.ts";
import { registerCollaborationTools, type PiExtensionApi } from "./collaboration.ts";
import { JobStatusBatcher } from "./job-status.ts";
import { retryAmbiguousStart } from "./start-retry.ts";

const PROVIDER_NAME = "openshell-tool-service";
const START_REQUEST_TIMEOUT_MS = 30_000;

const statusBatcher = new JobStatusBatcher(async (jobIds) => {
  const response = await request<{ jobs: ExternalJobHandle[] }>("/v1/jobs/status", {
    method: "POST",
    body: JSON.stringify({ jobIds }),
  });
  return response.jobs;
});

function configuration(): { baseUrl: string; token: string; sandboxName: string } {
  const baseUrl = process.env.POC_TOOL_SERVICE_URL?.replace(/\/$/, "");
  const token = process.env.POC_TOOL_SERVICE_TOKEN;
  const sandboxName = process.env.POC_CALLER_SANDBOX_NAME?.trim();
  const missing = [
    !baseUrl ? "POC_TOOL_SERVICE_URL" : undefined,
    !token ? "POC_TOOL_SERVICE_TOKEN" : undefined,
    !sandboxName ? "POC_CALLER_SANDBOX_NAME" : undefined,
  ].filter((name): name is string => name !== undefined);
  if (missing.length > 0) {
    throw new ExternalJobProviderError(
      `Missing required parent sandbox environment: ${missing.join(", ")}`,
      { code: "tool-service-config" },
    );
  }
  // The missing-field check above narrows all three values at runtime.
  if (!baseUrl || !token || !sandboxName) throw new Error("unreachable configuration state");
  return { baseUrl, token, sandboxName };
}

async function request<T>(
  path: string,
  init?: RequestInit,
  timeoutMilliseconds = 10_000,
): Promise<T> {
  const { baseUrl, token } = configuration();
  let response: Response;
  try {
    response = await fetch(`${baseUrl}${path}`, {
      ...init,
      headers: {
        authorization: `Bearer ${token}`,
        "content-type": "application/json",
        ...init?.headers,
      },
      signal: AbortSignal.timeout(timeoutMilliseconds),
    });
  } catch (error) {
    throw new ExternalJobProviderError(`OpenShell Tool Service request failed: ${String(error)}`, {
      code: "tool-service-unreachable",
      cause: error,
    });
  }

  if (!response.ok) {
    const body = (await response.text()).slice(0, 4096);
    throw new ExternalJobProviderError(
      `OpenShell Tool Service returned HTTP ${response.status}: ${body}`,
      { code: "tool-service-http" },
    );
  }
  return (await response.json()) as T;
}

async function start(input: ExternalJobStartInput): Promise<ExternalJobHandle> {
  const { sandboxName } = configuration();
  const childRequest = childRequestFromPrompt(input.prompt);
  if (!childRequest) {
    throw new ExternalJobProviderError(
      "The openshell-worker task must contain one non-empty <openshell-policy> block",
      { code: "child-policy-missing" },
    );
  }
  const idempotencyKey = idempotencyKeyFromPi(input);
  const participantAlias = childRequest.participantAlias ?? `worker-${input.stepIndex + 1}`;
  const parentWorkflowId = workflowIdFromPi(input);
  if (childRequest.coordination.mode === "all-ready" && !parentWorkflowId) {
    throw new ExternalJobProviderError(
      "A coordinated openshell-worker could not resolve Pi's shared parent workflow ID",
      { code: "workflow-correlation-missing" },
    );
  }
  const requestBody = JSON.stringify({
    idempotencyKey,
    caller: {
      sandboxName,
    },
    workflow: {
      id: parentWorkflowId ?? input.runId,
      startMode: childRequest.coordination.mode,
      expectedWorkers: childRequest.coordination.expectedWorkers,
    },
    worker: {
      stepIndex: input.stepIndex,
      role: participantAlias,
      prompt: childRequest.prompt,
      resources: {
        childPolicy: childRequest.childPolicy,
      },
    },
  });
  return retryAmbiguousStart(
    () =>
      request<ExternalJobHandle>(
        "/v1/jobs",
        {
          method: "POST",
          body: requestBody,
        },
        START_REQUEST_TIMEOUT_MS,
      ),
    (error) =>
      error instanceof ExternalJobProviderError && error.code === "tool-service-unreachable",
    ({ nextAttempt, attempts, delayMilliseconds }) => {
      console.warn(
        `OpenShell job creation response was ambiguous; retrying the same idempotent request ` +
          `(attempt=${nextAttempt}/${attempts}, delayMs=${delayMilliseconds})`,
      );
    },
  );
}

export default function registerOpenShellToolService(pi: PiExtensionApi): void {
  registerExternalJobProvider({
    name: PROVIDER_NAME,
    start,
    status: (providerJobId) => statusBatcher.get(providerJobId),
    result: (providerJobId) =>
      request<ExternalJobResult>(`/v1/jobs/${encodeURIComponent(providerJobId)}/result`),
    reattach: (providerJobId) =>
      request<ExternalJobHandle>(`/v1/jobs/${encodeURIComponent(providerJobId)}`),
  });
  registerCollaborationTools(pi);
}
