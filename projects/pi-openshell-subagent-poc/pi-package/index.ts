import {
  ExternalJobProviderError,
  registerExternalJobProvider,
  type ExternalJobHandle,
  type ExternalJobResult,
  type ExternalJobStartInput,
} from "pi-subagents/external-job-provider";
import { childRequestFromPrompt, idempotencyKeyFromPi } from "./resources.ts";

const PROVIDER_NAME = "openshell-tool-service";

function configuration(): { baseUrl: string; token: string; sandboxName: string } {
  const baseUrl = process.env.POC_TOOL_SERVICE_URL?.replace(/\/$/, "");
  const token = process.env.POC_TOOL_SERVICE_TOKEN;
  const sandboxName = process.env.POC_CALLER_SANDBOX_NAME?.trim();
  if (!baseUrl || !token || !sandboxName) {
    throw new ExternalJobProviderError(
      "POC_TOOL_SERVICE_URL, POC_TOOL_SERVICE_TOKEN, and POC_CALLER_SANDBOX_NAME are required",
      { code: "tool-service-config" },
    );
  }
  return { baseUrl, token, sandboxName };
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
  const { sandboxName } = configuration();
  const childRequest = childRequestFromPrompt(input.prompt);
  if (!childRequest) {
    throw new ExternalJobProviderError(
      "The openshell-worker task must contain one non-empty <openshell-policy> block",
      { code: "child-policy-missing" },
    );
  }
  const idempotencyKey = idempotencyKeyFromPi(input);
  return request<ExternalJobHandle>("/v1/jobs", {
    method: "POST",
    body: JSON.stringify({
      idempotencyKey,
      caller: {
        sandboxName,
      },
      prompt: childRequest.prompt,
      resources: {
        childPolicy: childRequest.childPolicy,
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
