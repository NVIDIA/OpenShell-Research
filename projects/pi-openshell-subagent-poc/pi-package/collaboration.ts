type ToolResult = {
  content: Array<{ type: "text"; text: string }>;
  details: Record<string, unknown>;
};

type ToolDefinition = {
  name: string;
  label: string;
  description: string;
  parameters: Record<string, unknown>;
  execute: (
    toolCallId: string,
    parameters: Record<string, unknown>,
  ) => Promise<ToolResult>;
};

export type PiExtensionApi = {
  registerTool: (definition: ToolDefinition) => void;
  sendMessage?: (
    message: {
      customType: string;
      content: string;
      display: boolean;
      details?: Record<string, unknown>;
    },
    options?: { triggerTurn?: boolean },
  ) => void;
  on?: (
    event: "session_start" | "session_shutdown",
    handler: () => void | Promise<void>,
  ) => void;
};

type CollaborationConfiguration = {
  baseUrl: string;
  token: string;
  role: "parent" | "child";
  sandboxName?: string;
};

type MailboxResponse = {
  deliveries: Array<{
    deliveryId: string;
    state: string;
    message: Record<string, unknown>;
  }>;
  terminalError?: {
    code: string;
    sender: { roleName: string; sandboxName: string };
    state: string;
    reason?: string | null;
    message: string;
  } | null;
};

function configuration(): CollaborationConfiguration | undefined {
  const childUrl = process.env.POC_COLLABORATION_URL?.replace(/\/$/, "");
  const childToken = process.env.POC_COLLABORATION_TOKEN;
  if (childUrl && childToken) {
    return { baseUrl: childUrl, token: childToken, role: "child" };
  }

  const parentUrl = process.env.POC_TOOL_SERVICE_URL?.replace(/\/$/, "");
  const parentToken = process.env.POC_TOOL_SERVICE_TOKEN;
  const sandboxName = process.env.POC_CALLER_SANDBOX_NAME?.trim();
  if (parentUrl && parentToken && sandboxName) {
    return { baseUrl: parentUrl, token: parentToken, role: "parent", sandboxName };
  }
  return undefined;
}

async function request<T>(
  path: string,
  init?: RequestInit,
  timeoutMilliseconds = 10_000,
  cancellationSignal?: AbortSignal,
): Promise<T> {
  const config = configuration();
  if (!config) {
    throw new Error(
      "Collaboration is not configured for this Pi process. Expected parent or child POC environment variables.",
    );
  }
  const headers: Record<string, string> = {
    authorization: `Bearer ${config.token}`,
    "content-type": "application/json",
  };
  if (config.sandboxName) {
    headers["x-poc-caller-sandbox-name"] = config.sandboxName;
  }
  const response = await fetch(`${config.baseUrl}${path}`, {
    ...init,
    headers: { ...headers, ...init?.headers },
    signal: cancellationSignal
      ? AbortSignal.any([AbortSignal.timeout(timeoutMilliseconds), cancellationSignal])
      : AbortSignal.timeout(timeoutMilliseconds),
  });
  if (!response.ok) {
    const body = (await response.text()).slice(0, 4096);
    throw new Error(`OpenShell Tool Service returned HTTP ${response.status}: ${body}`);
  }
  return (await response.json()) as T;
}

function result(value: unknown): ToolResult {
  return {
    content: [{ type: "text", text: JSON.stringify(value, null, 2) }],
    details: typeof value === "object" && value !== null ? (value as Record<string, unknown>) : {},
  };
}

const objectSchema = (
  properties: Record<string, unknown>,
  required: string[] = [],
): Record<string, unknown> => ({
  type: "object",
  properties,
  required,
  additionalProperties: false,
});

export function registerCollaborationTools(pi: PiExtensionApi): void {
  const config = configuration();
  if (!config) return;
  const automaticMailboxEnabled = config.role === "parent";

  let mailboxController: AbortController | undefined;
  let mailboxTask: Promise<void> | undefined;
  let manualWaitController: AbortController | undefined;
  let sessionActive = false;
  let manualWaitActive = false;

  const mailboxContent = (response: MailboxResponse): string =>
    response.deliveries
      .map((message) => {
        const envelope = message.message;
        const sender = envelope.sender as Record<string, unknown> | undefined;
        const role = String(sender?.roleName ?? sender?.sandboxName ?? "unknown");
        const sandbox = String(sender?.sandboxName ?? "unknown");
        const kind = String(envelope.kind ?? "message");
        return `[${kind}] Message from ${role} (${sandbox}):\n${String(envelope.body ?? "")}`;
      })
      .join("\n\n");

  const requiresParentTurn = (delivery: MailboxResponse["deliveries"][number]): boolean => {
    const message = delivery.message;
    if (message.kind === "question") return true;
    const envelope = message.envelope as Record<string, unknown> | undefined;
    const payload = envelope?.payload as Record<string, unknown> | undefined;
    if (payload?.actionRequired === true) return true;
    return new Set([
      "collaboration.error",
      "collaboration.failure",
      "collaboration.approval-required",
    ]).has(String(envelope?.type ?? ""));
  };

  const runMailbox = async (controller: AbortController): Promise<void> => {
    while (!controller.signal.aborted) {
      try {
        const response = await request<MailboxResponse>(
          "/v1/collaboration/mailbox?wait=1",
          undefined,
          5_000,
          controller.signal,
        );
        if (controller.signal.aborted) return;
        if (response.deliveries.length === 0) continue;
        const actionable = response.deliveries.filter(requiresParentTurn);
        if (actionable.length > 0) {
          const actionableResponse = { ...response, deliveries: actionable };
          pi.sendMessage?.(
            {
              customType: "openshell-collaboration-mailbox",
              content: mailboxContent(actionableResponse),
              display: true,
              details: actionableResponse,
            },
            { triggerTurn: true },
          );
        }
        await request(
          "/v1/collaboration/mailbox/ack",
          {
            method: "POST",
            body: JSON.stringify({
              deliveryIds: response.deliveries.map((delivery) => delivery.deliveryId),
            }),
          },
          10_000,
          controller.signal,
        );
      } catch (error) {
        if (controller.signal.aborted) return;
        console.warn(`OpenShell automatic mailbox retrying after error: ${String(error)}`);
        await new Promise((resolve) => setTimeout(resolve, 1_000));
      }
    }
  };

  const startMailbox = (): void => {
    if (
      !automaticMailboxEnabled ||
      !sessionActive ||
      manualWaitActive ||
      !pi.sendMessage ||
      mailboxController
    ) return;
    const controller = new AbortController();
    mailboxController = controller;
    mailboxTask = runMailbox(controller).finally(() => {
      if (mailboxController === controller) {
        mailboxController = undefined;
        mailboxTask = undefined;
      }
    });
  };

  const stopMailbox = async (): Promise<void> => {
    const controller = mailboxController;
    const task = mailboxTask;
    mailboxController = undefined;
    mailboxTask = undefined;
    controller?.abort();
    await task;
  };

  pi.on?.("session_start", () => {
    sessionActive = true;
    startMailbox();
  });
  pi.on?.("session_shutdown", async () => {
    sessionActive = false;
    manualWaitController?.abort();
    await stopMailbox();
  });

  pi.registerTool({
    name: "collaboration_list_participants",
    label: "List collaborators",
    description:
      "List active parent and child sandboxes in your collaboration group. Finished children are hidden unless explicitly requested.",
    parameters: objectSchema({
      includeFinished: {
        type: "boolean",
        description: "Include finished children for debugging. Defaults to false.",
      },
    }),
    async execute(_toolCallId, parameters) {
      const includeFinished = parameters.includeFinished === true;
      const query = includeFinished ? "?include_finished=true" : "";
      return result(await request(`/v1/collaboration/participants${query}`));
    },
  });

  pi.registerTool({
    name: "collaboration_send",
    label: "Send collaboration message",
    description:
      "Send a message to the parent or to a sibling child in the same parent-scoped collaboration group. Use recipient 'parent' to address the parent.",
    parameters: objectSchema(
      {
        recipient: {
          type: "string",
          description: "The recipient's stable role name, participant ID, or sandbox name.",
        },
        body: { type: "string", description: "Message body." },
        kind: {
          type: "string",
          enum: ["message", "progress", "question", "result"],
          description: "Message purpose. Defaults to message.",
        },
        type: {
          type: "string",
          description: "Typed envelope name. Defaults to collaboration.<kind>.",
        },
        correlationId: {
          type: "string",
          description: "Optional ID connecting a request and its reply.",
        },
        payload: {
          type: "object",
          description: "Optional machine-readable message data.",
          additionalProperties: true,
        },
        replyTo: { type: "string", description: "Optional message ID being answered." },
        idempotencyKey: {
          type: "string",
          description: "Optional stable key for retrying the same send without duplication.",
        },
      },
      ["recipient", "body"],
    ),
    async execute(_toolCallId, parameters) {
      const idempotencyKey =
        (parameters.idempotencyKey as string | undefined) ?? crypto.randomUUID();
      return result(
        await request("/v1/collaboration/messages", {
          method: "POST",
          body: JSON.stringify({
            recipient: parameters.recipient,
            body: parameters.body,
            kind: parameters.kind ?? "message",
            type: parameters.type,
            correlationId: parameters.correlationId ?? crypto.randomUUID(),
            payload: parameters.payload ?? {},
            replyTo: parameters.replyTo,
            idempotencyKey,
          }),
        }),
      );
    },
  });

  pi.registerTool({
    name: "collaboration_wait",
    label: "Wait for collaboration messages",
    description:
      "Long-poll for a collaboration message. When waiting on a specific worker, pass its stable role as sender so the wait fails immediately if that worker can no longer reply. One-shot workers must repeat after an empty timeout until the task deadline. Parents normally rely on automatic delivery. Returned messages are acknowledged before this tool completes.",
    parameters: objectSchema({
      sender: {
        type: "string",
        description:
          "Stable role name, participant ID, or sandbox name of the worker whose message is required.",
      },
      timeoutSeconds: {
        type: "number",
        minimum: 0,
        maximum: 30,
        description: "Long-poll for up to this many seconds. Defaults to 20.",
      },
    }),
    async execute(_toolCallId, parameters) {
      const wait = Number(parameters.timeoutSeconds ?? 20);
      const query = new URLSearchParams({ wait: String(wait) });
      if (typeof parameters.sender === "string" && parameters.sender.trim()) {
        query.set("sender", parameters.sender.trim());
      }
      manualWaitActive = true;
      await stopMailbox();
      const controller = new AbortController();
      manualWaitController = controller;
      try {
        const response = await request<MailboxResponse>(
          `/v1/collaboration/mailbox?${query.toString()}`,
          undefined,
          Math.max(10_000, (wait + 5) * 1_000),
          controller.signal,
        );
        if (response.deliveries.length > 0) {
          await request(
            "/v1/collaboration/mailbox/ack",
            {
              method: "POST",
              body: JSON.stringify({
                deliveryIds: response.deliveries.map((delivery) => delivery.deliveryId),
              }),
            },
            10_000,
            controller.signal,
          );
        }
        if (response.terminalError) {
          throw new Error(
            `${response.terminalError.code}: ${response.terminalError.message}`,
          );
        }
        return result(response);
      } finally {
        manualWaitController = undefined;
        manualWaitActive = false;
        startMailbox();
      }
    },
  });
}

export default function register(pi: PiExtensionApi): void {
  registerCollaborationTools(pi);
}
