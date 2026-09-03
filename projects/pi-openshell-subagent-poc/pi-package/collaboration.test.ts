import assert from "node:assert/strict";
import test from "node:test";
import {
  registerCollaborationTools,
  type PiExtensionApi,
} from "./collaboration.ts";

type RegisteredTool = Parameters<PiExtensionApi["registerTool"]>[0];

function restoreEnvironment(name: string, value: string | undefined): void {
  if (value === undefined) {
    delete process.env[name];
  } else {
    process.env[name] = value;
  }
}

test("registers parent collaboration tools and sends parent identity headers", async () => {
  const previousUrl = process.env.POC_TOOL_SERVICE_URL;
  const previousToken = process.env.POC_TOOL_SERVICE_TOKEN;
  const previousName = process.env.POC_CALLER_SANDBOX_NAME;
  const previousFetch = globalThis.fetch;
  process.env.POC_TOOL_SERVICE_URL = "http://tool-service:8765/";
  process.env.POC_TOOL_SERVICE_TOKEN = "parent-token";
  process.env.POC_CALLER_SANDBOX_NAME = "pi-parent";

  const tools = new Map<string, RegisteredTool>();
  const requests: Array<{ url: string; init?: RequestInit }> = [];
  globalThis.fetch = async (input, init) => {
    requests.push({ url: String(input), init });
    return new Response(JSON.stringify({ participants: [] }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  };

  try {
    registerCollaborationTools({
      registerTool(tool) {
        tools.set(tool.name, tool);
      },
    });
    assert.deepEqual([...tools.keys()], [
      "collaboration_list_participants",
      "collaboration_send",
      "collaboration_wait",
    ]);
    await tools.get("collaboration_list_participants")!.execute("call-1", {});
    assert.equal(requests[0].url, "http://tool-service:8765/v1/collaboration/participants");
    const headers = requests[0].init!.headers as Record<string, string>;
    assert.equal(headers.authorization, "Bearer parent-token");
    assert.equal(headers["x-poc-caller-sandbox-name"], "pi-parent");
  } finally {
    globalThis.fetch = previousFetch;
    process.env.POC_TOOL_SERVICE_URL = previousUrl;
    process.env.POC_TOOL_SERVICE_TOKEN = previousToken;
    process.env.POC_CALLER_SANDBOX_NAME = previousName;
  }
});

test("automatic mailbox delivers actionable questions, triggers a turn, and acknowledges", async () => {
  const previousUrl = process.env.POC_TOOL_SERVICE_URL;
  const previousToken = process.env.POC_TOOL_SERVICE_TOKEN;
  const previousName = process.env.POC_CALLER_SANDBOX_NAME;
  const previousFetch = globalThis.fetch;
  process.env.POC_TOOL_SERVICE_URL = "http://tool-service:8765";
  process.env.POC_TOOL_SERVICE_TOKEN = "parent-token";
  process.env.POC_CALLER_SANDBOX_NAME = "pi-parent";

  const tools = new Map<string, RegisteredTool>();
  const handlers = new Map<string, () => void | Promise<void>>();
  const notifications: Array<{
    message: { customType: string; content: string };
    options?: { triggerTurn?: boolean };
  }> = [];
  const requests: Array<{ url: string; init?: RequestInit }> = [];
  let acknowledge!: () => void;
  const acknowledged = new Promise<void>((resolve) => {
    acknowledge = resolve;
  });
  globalThis.fetch = async (input, init) => {
    const url = String(input);
    requests.push({ url, init });
    if (url.endsWith("/mailbox/ack")) {
      acknowledge();
      return new Response(JSON.stringify({ acknowledgedDeliveryIds: ["delivery-7"] }), { status: 200 });
    }
    if (requests.filter((request) => request.url.includes("/mailbox?")).length === 1) {
      return new Response(
        JSON.stringify({
          deliveries: [{
            deliveryId: "delivery-7",
            state: "queued",
            message: {
              sequence: 7, kind: "question", body: "ASYNC_HELLO",
              sender: { roleName: "worker-a", sandboxName: "pi-child-a" },
            },
          }],
        }),
        { status: 200 },
      );
    }
    return new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new Error("aborted")));
    });
  };

  try {
    registerCollaborationTools({
      registerTool: (tool) => tools.set(tool.name, tool),
      sendMessage: (message, options) => notifications.push({ message, options }),
      on: (event, handler) => handlers.set(event, handler),
    });
    await handlers.get("session_start")!();
    await acknowledged;
    await handlers.get("session_shutdown")!();
    assert.equal(notifications.length, 1);
    assert.equal(notifications[0].message.customType, "openshell-collaboration-mailbox");
    assert.match(notifications[0].message.content, /ASYNC_HELLO/);
    assert.match(notifications[0].message.content, /worker-a/);
    assert.equal(notifications[0].options?.triggerTurn, true);
    assert.equal(requests[0].url, "http://tool-service:8765/v1/collaboration/mailbox?wait=1");
    assert.equal(requests[1].url, "http://tool-service:8765/v1/collaboration/mailbox/ack");
    assert.deepEqual(JSON.parse(String(requests[1].init?.body)), {
      deliveryIds: ["delivery-7"],
    });
  } finally {
    globalThis.fetch = previousFetch;
    process.env.POC_TOOL_SERVICE_URL = previousUrl;
    process.env.POC_TOOL_SERVICE_TOKEN = previousToken;
    process.env.POC_CALLER_SANDBOX_NAME = previousName;
  }
});

test("automatic mailbox silently acknowledges informational messages", async () => {
  const previousUrl = process.env.POC_TOOL_SERVICE_URL;
  const previousToken = process.env.POC_TOOL_SERVICE_TOKEN;
  const previousName = process.env.POC_CALLER_SANDBOX_NAME;
  const previousFetch = globalThis.fetch;
  process.env.POC_TOOL_SERVICE_URL = "http://tool-service:8765";
  process.env.POC_TOOL_SERVICE_TOKEN = "parent-token";
  process.env.POC_CALLER_SANDBOX_NAME = "pi-parent";

  const tools = new Map<string, RegisteredTool>();
  const handlers = new Map<string, () => void | Promise<void>>();
  const notifications: Array<{ content: string }> = [];
  let acknowledge!: () => void;
  const acknowledged = new Promise<void>((resolve) => {
    acknowledge = resolve;
  });
  let mailboxReads = 0;
  globalThis.fetch = async (input, init) => {
    const url = String(input);
    if (url.endsWith("/mailbox/ack")) {
      assert.deepEqual(JSON.parse(String(init?.body)), {
        deliveryIds: ["message-delivery", "progress-delivery", "result-delivery"],
      });
      acknowledge();
      return new Response(JSON.stringify({ acknowledgedDeliveryIds: [] }), { status: 200 });
    }
    mailboxReads += 1;
    if (mailboxReads === 1) {
      return new Response(JSON.stringify({
        deliveries: [
          {
            deliveryId: "message-delivery",
            state: "queued",
            message: { kind: "message", body: "hello", sender: { roleName: "worker-a" } },
          },
          {
            deliveryId: "progress-delivery",
            state: "queued",
            message: { kind: "progress", body: "halfway", sender: { roleName: "worker-b" } },
          },
          {
            deliveryId: "result-delivery",
            state: "queued",
            message: { kind: "result", body: "done", sender: { roleName: "worker-c" } },
          },
        ],
      }), { status: 200 });
    }
    return new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new Error("aborted")));
    });
  };

  try {
    registerCollaborationTools({
      registerTool: (tool) => tools.set(tool.name, tool),
      sendMessage: (message) => notifications.push({ content: message.content }),
      on: (event, handler) => handlers.set(event, handler),
    });
    await handlers.get("session_start")!();
    await acknowledged;
    await handlers.get("session_shutdown")!();
    assert.equal(notifications.length, 0);
  } finally {
    globalThis.fetch = previousFetch;
    restoreEnvironment("POC_TOOL_SERVICE_URL", previousUrl);
    restoreEnvironment("POC_TOOL_SERVICE_TOKEN", previousToken);
    restoreEnvironment("POC_CALLER_SANDBOX_NAME", previousName);
  }
});

test("automatic mailbox wakes the parent for an explicit actionRequired payload", async () => {
  const previousUrl = process.env.POC_TOOL_SERVICE_URL;
  const previousToken = process.env.POC_TOOL_SERVICE_TOKEN;
  const previousName = process.env.POC_CALLER_SANDBOX_NAME;
  const previousFetch = globalThis.fetch;
  process.env.POC_TOOL_SERVICE_URL = "http://tool-service:8765";
  process.env.POC_TOOL_SERVICE_TOKEN = "parent-token";
  process.env.POC_CALLER_SANDBOX_NAME = "pi-parent";

  const tools = new Map<string, RegisteredTool>();
  const handlers = new Map<string, () => void | Promise<void>>();
  const notifications: Array<{ content: string; triggerTurn?: boolean }> = [];
  let acknowledge!: () => void;
  const acknowledged = new Promise<void>((resolve) => {
    acknowledge = resolve;
  });
  let mailboxReads = 0;
  globalThis.fetch = async (input, init) => {
    const url = String(input);
    if (url.endsWith("/mailbox/ack")) {
      acknowledge();
      return new Response(JSON.stringify({ acknowledgedDeliveryIds: ["delivery-action"] }), { status: 200 });
    }
    mailboxReads += 1;
    if (mailboxReads === 1) {
      return new Response(JSON.stringify({
        deliveries: [{
          deliveryId: "delivery-action",
          state: "queued",
          message: {
            kind: "message",
            body: "approval needed",
            sender: { roleName: "worker-a", sandboxName: "pi-child-a" },
            envelope: { type: "collaboration.message", payload: { actionRequired: true } },
          },
        }],
      }), { status: 200 });
    }
    return new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new Error("aborted")));
    });
  };

  try {
    registerCollaborationTools({
      registerTool: (tool) => tools.set(tool.name, tool),
      sendMessage: (message, options) => notifications.push({
        content: message.content,
        triggerTurn: options?.triggerTurn,
      }),
      on: (event, handler) => handlers.set(event, handler),
    });
    await handlers.get("session_start")!();
    await acknowledged;
    await handlers.get("session_shutdown")!();
    assert.equal(notifications.length, 1);
    assert.match(notifications[0].content, /approval needed/);
    assert.equal(notifications[0].triggerTurn, true);
  } finally {
    globalThis.fetch = previousFetch;
    restoreEnvironment("POC_TOOL_SERVICE_URL", previousUrl);
    restoreEnvironment("POC_TOOL_SERVICE_TOKEN", previousToken);
    restoreEnvironment("POC_CALLER_SANDBOX_NAME", previousName);
  }
});

test("child uses collaboration_wait without starting an automatic mailbox", async () => {
  const previousChildUrl = process.env.POC_COLLABORATION_URL;
  const previousChildToken = process.env.POC_COLLABORATION_TOKEN;
  const previousParentUrl = process.env.POC_TOOL_SERVICE_URL;
  const previousParentToken = process.env.POC_TOOL_SERVICE_TOKEN;
  const previousParentName = process.env.POC_CALLER_SANDBOX_NAME;
  const previousFetch = globalThis.fetch;
  process.env.POC_COLLABORATION_URL = "http://tool-service:8765";
  process.env.POC_COLLABORATION_TOKEN = "child-token";
  delete process.env.POC_TOOL_SERVICE_URL;
  delete process.env.POC_TOOL_SERVICE_TOKEN;
  delete process.env.POC_CALLER_SANDBOX_NAME;

  const tools = new Map<string, RegisteredTool>();
  const handlers = new Map<string, () => void | Promise<void>>();
  const notifications: Array<{ content: string }> = [];
  const requests: Array<{ url: string; init?: RequestInit }> = [];
  globalThis.fetch = async (input, init) => {
    const url = String(input);
    requests.push({ url, init });
    if (url.endsWith("/mailbox/ack")) {
      return new Response(JSON.stringify({ acknowledgedDeliveryIds: ["delivery-child"] }), {
        status: 200,
      });
    }
    return new Response(
      JSON.stringify({
        deliveries: [{
          deliveryId: "delivery-child",
          state: "queued",
          message: {
            sequence: 12, kind: "message", body: "ALREADY_QUEUED",
            sender: { roleName: "worker-a", sandboxName: "pi-child-a" },
          },
        }],
      }),
      { status: 200 },
    );
  };

  try {
    registerCollaborationTools({
      registerTool: (tool) => tools.set(tool.name, tool),
      sendMessage: (message) => notifications.push({ content: message.content }),
      on: (event, handler) => handlers.set(event, handler),
    });
    await handlers.get("session_start")!();
    assert.equal(requests.length, 0, "child session startup must not poll the mailbox");

    const waited = await tools.get("collaboration_wait")!.execute("call-child", {
      timeoutSeconds: 20,
    });
    await handlers.get("session_shutdown")!();

    assert.match(waited.content[0].text, /ALREADY_QUEUED/);
    assert.equal(notifications.length, 0);
    assert.equal(requests[0].url, "http://tool-service:8765/v1/collaboration/mailbox?wait=20");
    assert.equal(requests[1].url, "http://tool-service:8765/v1/collaboration/mailbox/ack");
    assert.deepEqual(JSON.parse(String(requests[1].init?.body)), {
      deliveryIds: ["delivery-child"],
    });
  } finally {
    globalThis.fetch = previousFetch;
    restoreEnvironment("POC_COLLABORATION_URL", previousChildUrl);
    restoreEnvironment("POC_COLLABORATION_TOKEN", previousChildToken);
    restoreEnvironment("POC_TOOL_SERVICE_URL", previousParentUrl);
    restoreEnvironment("POC_TOOL_SERVICE_TOKEN", previousParentToken);
    restoreEnvironment("POC_CALLER_SANDBOX_NAME", previousParentName);
  }
});

test("collaboration_wait fails immediately when its expected sender is terminal", async () => {
  const previousChildUrl = process.env.POC_COLLABORATION_URL;
  const previousChildToken = process.env.POC_COLLABORATION_TOKEN;
  const previousFetch = globalThis.fetch;
  process.env.POC_COLLABORATION_URL = "http://tool-service:8765";
  process.env.POC_COLLABORATION_TOKEN = "child-token";

  const tools = new Map<string, RegisteredTool>();
  const requests: string[] = [];
  globalThis.fetch = async (input) => {
    requests.push(String(input));
    return new Response(
      JSON.stringify({
        deliveries: [],
        terminalError: {
          code: "expected-sender-failed",
          sender: { roleName: "worker-b", sandboxName: "pi-child-b" },
          state: "failed",
          reason: "SSH transport failed",
          message: "Expected worker worker-b failed: SSH transport failed",
        },
      }),
      { status: 200 },
    );
  };

  try {
    registerCollaborationTools({
      registerTool: (tool) => tools.set(tool.name, tool),
    });
    await assert.rejects(
      tools.get("collaboration_wait")!.execute("call-terminal", {
        sender: "worker-b",
        timeoutSeconds: 20,
      }),
      /expected-sender-failed: Expected worker worker-b failed: SSH transport failed/,
    );
    assert.equal(
      requests[0],
      "http://tool-service:8765/v1/collaboration/mailbox?wait=20&sender=worker-b",
    );
  } finally {
    globalThis.fetch = previousFetch;
    restoreEnvironment("POC_COLLABORATION_URL", previousChildUrl);
    restoreEnvironment("POC_COLLABORATION_TOKEN", previousChildToken);
  }
});

test("blocking wait pauses automatic delivery and consumes the same mailbox once", async () => {
  const previousUrl = process.env.POC_TOOL_SERVICE_URL;
  const previousToken = process.env.POC_TOOL_SERVICE_TOKEN;
  const previousName = process.env.POC_CALLER_SANDBOX_NAME;
  const previousFetch = globalThis.fetch;
  process.env.POC_TOOL_SERVICE_URL = "http://tool-service:8765";
  process.env.POC_TOOL_SERVICE_TOKEN = "parent-token";
  process.env.POC_CALLER_SANDBOX_NAME = "pi-parent";

  const tools = new Map<string, RegisteredTool>();
  const handlers = new Map<string, () => void | Promise<void>>();
  const notifications: Array<{ content: string }> = [];
  const requests: Array<{ url: string; init?: RequestInit }> = [];
  let mailboxRead = 0;
  globalThis.fetch = async (input, init) => {
    const url = String(input);
    requests.push({ url, init });
    if (url.endsWith("/mailbox/ack")) {
      return new Response(JSON.stringify({ acknowledgedDeliveryIds: ["delivery-11"] }), { status: 200 });
    }
    mailboxRead += 1;
    if (mailboxRead === 2) {
      return new Response(
        JSON.stringify({
          deliveries: [{
            deliveryId: "delivery-11",
            state: "queued",
            message: {
              sequence: 11, kind: "message", body: "ONLY_ONCE",
              sender: { roleName: "worker-a", sandboxName: "pi-child-a" },
            },
          }],
        }),
        { status: 200 },
      );
    }
    return new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new Error("aborted")));
    });
  };

  try {
    registerCollaborationTools({
      registerTool: (tool) => tools.set(tool.name, tool),
      sendMessage: (message) => notifications.push({ content: message.content }),
      on: (event, handler) => handlers.set(event, handler),
    });
    await handlers.get("session_start")!();
    const waited = await tools.get("collaboration_wait")!.execute("call-1", {
      timeoutSeconds: 20,
    });
    await handlers.get("session_shutdown")!();

    assert.match(waited.content[0].text, /ONLY_ONCE/);
    assert.equal(notifications.length, 0);
    assert.equal(requests[0].url, "http://tool-service:8765/v1/collaboration/mailbox?wait=1");
    assert.equal(requests[1].url, "http://tool-service:8765/v1/collaboration/mailbox?wait=20");
    assert.equal(requests[2].url, "http://tool-service:8765/v1/collaboration/mailbox/ack");
    assert.deepEqual(JSON.parse(String(requests[2].init?.body)), {
      deliveryIds: ["delivery-11"],
    });
  } finally {
    globalThis.fetch = previousFetch;
    process.env.POC_TOOL_SERVICE_URL = previousUrl;
    process.env.POC_TOOL_SERVICE_TOKEN = previousToken;
    process.env.POC_CALLER_SANDBOX_NAME = previousName;
  }
});

test("child collaboration configuration does not claim a parent sandbox name", async () => {
  const previousChildUrl = process.env.POC_COLLABORATION_URL;
  const previousChildToken = process.env.POC_COLLABORATION_TOKEN;
  const previousParentUrl = process.env.POC_TOOL_SERVICE_URL;
  const previousFetch = globalThis.fetch;
  process.env.POC_COLLABORATION_URL = "http://tool-service:8765";
  process.env.POC_COLLABORATION_TOKEN = "child-token";
  delete process.env.POC_TOOL_SERVICE_URL;

  const tools = new Map<string, RegisteredTool>();
  let requestHeaders: Record<string, string> = {};
  globalThis.fetch = async (_input, init) => {
    requestHeaders = init!.headers as Record<string, string>;
    return new Response(JSON.stringify({ messageId: "message-1" }), { status: 201 });
  };

  try {
    registerCollaborationTools({ registerTool: (tool) => tools.set(tool.name, tool) });
    await tools.get("collaboration_send")!.execute("call-1", {
      recipient: "parent",
      body: "done",
      idempotencyKey: "send-1",
    });
    assert.equal(requestHeaders.authorization, "Bearer child-token");
    assert.equal(requestHeaders["x-poc-caller-sandbox-name"], undefined);
  } finally {
    globalThis.fetch = previousFetch;
    process.env.POC_COLLABORATION_URL = previousChildUrl;
    process.env.POC_COLLABORATION_TOKEN = previousChildToken;
    process.env.POC_TOOL_SERVICE_URL = previousParentUrl;
  }
});
