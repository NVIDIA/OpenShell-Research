import {
  ExternalJobProviderError,
  registerExternalJobProvider,
  type ExternalJobHandle,
  type ExternalJobResult,
  type ExternalJobStartInput,
} from "pi-subagents/external-job-provider";
import { childPolicyFromPrompt, githubRepositoriesFromPrompt } from "./resources.ts";

const PROVIDER_NAME = "openshell-tool-service";

function configuration(): { baseUrl: string; token: string } {
  const baseUrl = process.env.POC_TOOL_SERVICE_URL?.replace(/\/$/, "");
  const token = process.env.POC_TOOL_SERVICE_TOKEN;
  if (!baseUrl || !token) {
    throw new ExternalJobProviderError(
      "POC_TOOL_SERVICE_URL and POC_TOOL_SERVICE_TOKEN are required",
      { code: "tool-service-config" },
    );
  }
  return { baseUrl, token };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
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
      signal: AbortSignal.timeout(10_000),
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
  const childPolicy = childPolicyFromPrompt(input.prompt);
  if (!childPolicy) {
    throw new ExternalJobProviderError(
      "The openshell-worker task must contain one non-empty <openshell-policy> block",
      { code: "child-policy-missing" },
    );
  }
  return request<ExternalJobHandle>("/v1/jobs", {
    method: "POST",
    body: JSON.stringify({
      runId: input.runId,
      stepIndex: input.stepIndex,
      agent: input.agent,
      prompt: input.prompt,
      promptDigest: input.promptDigest,
      options: input.options,
      resources: {
        githubRepositories: githubRepositoriesFromPrompt(input.prompt),
        childPolicy,
      },
    }),
  });
}

export default function registerOpenShellToolService(): void {
  registerExternalJobProvider({
    name: PROVIDER_NAME,
    start,
    status: (providerJobId) =>
      request<ExternalJobHandle>(`/v1/jobs/${encodeURIComponent(providerJobId)}`),
    result: (providerJobId) =>
      request<ExternalJobResult>(`/v1/jobs/${encodeURIComponent(providerJobId)}/result`),
    reattach: (providerJobId) =>
      request<ExternalJobHandle>(`/v1/jobs/${encodeURIComponent(providerJobId)}`),
  });
}
