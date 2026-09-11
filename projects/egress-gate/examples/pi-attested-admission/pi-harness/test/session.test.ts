// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, writeFile, readdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import {
  createAssistantMessageEventStream,
  type AssistantMessage,
  type Context,
  type Model,
} from "@earendil-works/pi-ai";
import type { StreamFn } from "@earendil-works/pi-agent-core";
import {
  Admission,
  type AdmissionKind,
  type AdmissionResponse,
  type Evaluate,
} from "../src/admission.js";
import {
  AdmissionSession,
  createAdmissionRuntime,
  projectTools,
} from "../src/session.js";

const model: Model<"openai-completions"> = {
  id: "test",
  name: "Test",
  provider: "test",
  api: "openai-completions",
  baseUrl: "https://provider.test/v1",
  reasoning: false,
  input: ["text"],
  contextWindow: 100000,
  maxTokens: 4096,
  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
};
const allow: AdmissionResponse = {
  decision: "allow",
  replacement: null,
  receipt: null,
};
const deny: AdmissionResponse = {
  decision: "deny",
  replacement: null,
  receipt: null,
};

function answer(
  text: string,
  calls: {
    id: string;
    name: string;
    arguments: Record<string, unknown>;
  }[] = [],
): AssistantMessage {
  return {
    role: "assistant",
    content: [
      { type: "text", text },
      ...calls.map((call) => ({ type: "toolCall" as const, ...call })),
    ],
    api: model.api,
    model: model.id,
    provider: model.provider,
    timestamp: Date.now(),
    stopReason: calls.length ? "toolUse" : "stop",
    usage: {
      input: 1,
      output: 1,
      cacheRead: 0,
      cacheWrite: 0,
      totalTokens: 2,
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
    },
  };
}

async function fixture(
  evaluate?: Evaluate,
  responses: AssistantMessage[] = [answer("Done")],
  compactAtTokens?: number,
) {
  const cwd = await mkdtemp(join(tmpdir(), "pi-admission-test-"));
  await writeFile(join(cwd, "AGENTS.md"), "Use the project tools.");
  await mkdir(join(cwd, ".pi/skills/example"), { recursive: true });
  await writeFile(
    join(cwd, ".pi/skills/example/SKILL.md"),
    "---\nname: example\ndescription: Example skill\n---\nSKILL_CANDIDATE",
  );
  const requests: Context[] = [];
  const kinds: AdmissionKind[] = [];
  const stream: StreamFn = (_model, context, options) => {
    assert.ok(options?.headers?.["x-egress-admission"]);
    requests.push(structuredClone({ ...context, tools: undefined }));
    const response = responses.shift();
    assert.ok(response, "unexpected additional model call");
    const result = createAssistantMessageEventStream();
    result.push({
      type: "done",
      reason: response.stopReason === "toolUse" ? "toolUse" : "stop",
      message: response,
    });
    return result;
  };
  const admission = new Admission(async (kind, body, signal) => {
    kinds.push(kind);
    return (
      (await evaluate?.(kind, body, signal)) ??
      (kind === "provider_context" ? { ...allow, receipt: "receipt" } : allow)
    );
  });
  const session = await AdmissionSession.create({
    cwd,
    sessionDir: join(cwd, "sessions"),
    agentDir: join(cwd, "agent"),
    model,
    apiKey: "placeholder",
    admission,
    stream,
    compactAtTokens,
  });
  session.settingsManager.applyOverrides({ compaction: { keepRecentTokens: 5 } });
  return { session, cwd, requests, kinds };
}

async function disk(session: AdmissionSession): Promise<string> {
  try {
    return await readFile(session.sessionFile, "utf8");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return "";
    throw error;
  }
}

for (const [kind, marker, prompt, responses] of [
  ["user_message", "CANDIDATE", "CANDIDATE", [answer("done")]],
  ["user_message", "SKILL_CANDIDATE", "/skill:example", [answer("done")]],
  ["assistant_message", "CANDIDATE", "hello", [answer("CANDIDATE")]],
  ["assistant_message", "REASONING_CANDIDATE", "hello", [{
    ...answer("done"),
    content: [{ type: "thinking", thinking: "REASONING_CANDIDATE" }, { type: "text", text: "done" }] as AssistantMessage["content"],
  }]],
  [
    "tool_result",
    "CANDIDATE",
    "read",
    [
      answer("", [
        { id: "call", name: "read", arguments: { path: "candidate.txt" } },
      ]),
    ],
  ],
  [
    "tool_result",
    "Requested tool is not available",
    "read",
    [answer("", [{ id: "call", name: "missing", arguments: {} }])],
  ],
] as const) {
  test(`pending and denied ${kind}: ${marker}`, async () => {
    let release!: (result: AdmissionResponse) => void;
    let reached!: () => void;
    const pending = new Promise<AdmissionResponse>((resolve) => {
      release = resolve;
    });
    const seen = new Promise<void>((resolve) => {
      reached = resolve;
    });
    const { session, cwd } = await fixture(
      async (current, body) => {
        if (current === kind && JSON.stringify(body).includes(marker)) {
          reached();
          return pending;
        }
        return current === "provider_context"
          ? { ...allow, receipt: "receipt" }
          : allow;
      },
      [...responses],
    );
    const events: unknown[] = [];
    session.subscribe((event) => {
      events.push(structuredClone(event));
    });
    await writeFile(join(cwd, "candidate.txt"), "CANDIDATE");
    const run = session.prompt(prompt);
    await seen;
    assert.ok(!JSON.stringify(session.entries).includes(marker));
    assert.ok(!JSON.stringify(session.history).includes(marker));
    assert.ok(!JSON.stringify(session.messages).includes(marker));
    assert.ok(!JSON.stringify(events).includes(marker));
    assert.ok(!(await disk(session)).includes(marker));
    release(deny);
    await assert.rejects(run);
    assert.ok(!JSON.stringify(events).includes(marker));
    assert.ok(!JSON.stringify(session.history).includes(marker));
    assert.ok(!(await disk(session)).includes(marker));
  });
}

for (const [shape, edits] of [
  ["JSON string", { edits: JSON.stringify([{ oldText: "before", newText: "after" }]) }],
  ["single edit", { edits: { oldText: "before", newText: "after" } }],
  ["legacy", { oldText: "before", newText: "after" }],
] as const) {
  test(`native edit preparation preserves approved ${shape} arguments`, async () => {
    const args = { path: "edit.txt", ...edits };
    const { session, cwd } = await fixture(undefined, [
      answer("", [{ id: "edit", name: "edit", arguments: args }]),
      answer("Done"),
    ]);
    await writeFile(join(cwd, "edit.txt"), "before");
    await session.prompt("Edit the file");
    assert.equal(await readFile(join(cwd, "edit.txt"), "utf8"), "after");
    const assistant = session.history.find(
      (message) => message.role === "assistant",
    )!;
    const call = assistant.content.find((block) => block.type === "toolCall")!;
    assert.deepEqual(call.arguments, args);
    const saved = (await disk(session))
      .trim().split("\n").map((line) => JSON.parse(line));
    assert.deepEqual(
      saved.find((entry) => entry.message?.role === "assistant").message.content
        .find((block: { type: string }) => block.type === "toolCall").arguments,
      args,
    );
  });
}

for (const decision of ["deny", "replace"] as const) {
  test(`split-turn compaction waits for admission: ${decision}`, async () => {
    let release!: (result: AdmissionResponse) => void;
    let reached!: () => void;
    const pending = new Promise<AdmissionResponse>((resolve) => { release = resolve; });
    const seen = new Promise<void>((resolve) => { reached = resolve; });
    let candidate: Record<string, unknown> = {};
    const { session, cwd } = await fixture(async (kind, body) => {
      if (kind === "compaction_summary") {
        candidate = body;
        reached();
        return pending;
      }
      return kind === "provider_context" ? { ...allow, receipt: "receipt" } : allow;
    }, [
      answer("", [{ id: "read", name: "read", arguments: { path: "notes.txt" } }]),
      answer("Done"),
      answer("RAW_SUMMARY"),
    ]);
    await writeFile(join(cwd, "notes.txt"), "Approved note");
    await session.prompt("Read notes.txt");
    session.settingsManager.applyOverrides({ compaction: { keepRecentTokens: 1 } });
    const before = structuredClone(session.history);
    const saved = await disk(session);
    const compact = session.compact();
    const settled = decision === "deny" ? assert.rejects(compact) : compact;
    await seen;
    // Native compaction appends file-operation text; that must be admitted too.
    assert.ok(String(candidate.text).includes("RAW_SUMMARY"));
    assert.ok(String(candidate.text).includes("notes.txt"));
    assert.deepEqual(session.history, before);
    assert.equal(await disk(session), saved);
    release({
      decision,
      replacement: decision === "replace" ? { ...candidate, text: "APPROVED_SUMMARY" } : null,
      receipt: null,
    });
    await settled;
    if (decision === "deny") {
      assert.deepEqual(session.history, before);
      assert.equal(await disk(session), saved);
    } else {
      const entry = session.entries.find((entry) => entry.type === "compaction")!;
      assert.equal(entry.summary, "APPROVED_SUMMARY");
      assert.equal(entry.details, undefined);
      assert.ok(JSON.stringify(session.history).includes("APPROVED_SUMMARY"));
      assert.ok((await disk(session)).includes("Read notes.txt"), "history is append-only");
    }
    for (const snapshot of [JSON.stringify(session.history), await disk(session)])
      assert.ok(!snapshot.includes("RAW_SUMMARY"));
  });
}

test("real bash is bounded before Pi can spill an unchecked output log", async () => {
  const cwd = await mkdtemp(join(tmpdir(), "pi-bash-test-"));
  const before = (await readdir(tmpdir())).filter((name) =>
    name.startsWith("pi-bash-"),
  );
  const bash = projectTools(cwd).find((tool) => tool.name === "bash")!;
  try {
    await bash.execute("call", { command: "head -c 100000 /dev/zero" });
  } catch {
    /* Pi reports the bounded operation as a tool error. */
  }
  const after = (await readdir(tmpdir())).filter((name) =>
    name.startsWith("pi-bash-"),
  );
  assert.deepEqual(after, before);
});

test("provider errors and partial responses never become history", async () => {
  const error = {
    ...answer("PARTIAL_RESPONSE"),
    stopReason: "error" as const,
    errorMessage: "RAW_PROVIDER_ERROR",
  };
  const { session } = await fixture(undefined, [error]);
  await assert.rejects(session.prompt("hello"));
  assert.equal(session.history.length, 1);
  assert.ok(!JSON.stringify(session.entries).includes("PARTIAL_RESPONSE"));
  assert.ok(!(await disk(session)).includes("RAW_PROVIDER_ERROR"));
});

test("denied tool result stops model continuation and unexecuted calls", async () => {
  const { session, cwd, requests } = await fixture(
    async (kind, body) => {
      if (
        kind === "tool_result" &&
        JSON.stringify(body).includes("FORBIDDEN_OUTPUT")
      )
        return deny;
      return kind === "provider_context"
        ? { ...allow, receipt: "receipt" }
        : allow;
    },
    [
      answer("", [
        { id: "read", name: "read", arguments: { path: "candidate.txt" } },
        {
          id: "write",
          name: "write",
          arguments: {
            path: "must-not-exist",
            content: "unchecked continuation",
          },
        },
      ]),
    ],
  );
  await writeFile(join(cwd, "candidate.txt"), "FORBIDDEN_OUTPUT");
  await assert.rejects(session.prompt("Use the tools"));
  assert.equal(requests.length, 1);
  assert.equal(
    session.history.filter((message) => message.role === "toolResult").length,
    2,
  );
  await assert.rejects(readFile(join(cwd, "must-not-exist")));
  assert.ok(!JSON.stringify(session.entries).includes("FORBIDDEN_OUTPUT"));
});

test("unavailable tool admission leaves an unfinished session stopped", async () => {
  const { session, requests } = await fixture(
    async (kind) => {
      if (kind === "tool_result") throw new Error("SERVICE_UNAVAILABLE");
      return kind === "provider_context"
        ? { ...allow, receipt: "receipt" }
        : allow;
    },
    [answer("", [{ id: "call", name: "missing", arguments: {} }])],
  );
  await assert.rejects(session.prompt("hello"));
  assert.equal(session.isStopped, true);
  assert.equal(requests.length, 1);
  await assert.rejects(session.prompt("continue"));
  assert.equal(
    session.history.filter((message) => message.role === "toolResult").length,
    0,
  );
});

test("project context is checked before any model request or message write", async () => {
  await assert.rejects(
    fixture(async (kind, body) => {
      assert.equal(kind, "system_context");
      assert.ok(String(body.text).includes("Use the project tools."));
      assert.ok(String(body.text).includes("Example skill"));
      return deny;
    }),
  );
});

test("cancelled admission cannot append even when the service subsequently allows", async () => {
  const { session, requests } = await fixture(async (kind) => {
    if (kind === "user_message") session.agent.abort();
    return allow;
  });
  await assert.rejects(session.prompt("CANCELLED"));
  assert.deepEqual(session.entries, []);
  assert.equal(requests.length, 0);
});

for (const enabled of [true, false]) {
  test(`overflow respects auto-compaction ${enabled}`, async () => {
    const overflow = {
      ...answer(""),
      stopReason: "error" as const,
      errorMessage: "exceeds the context window",
    };
    const { session, requests, kinds } = await fixture(undefined, [
      answer("first"),
      overflow,
      answer("summary"),
      answer("retry result"),
    ]);
    await session.prompt("first turn");
    session.setAutoCompactionEnabled(enabled);
    session.settingsManager.applyOverrides({ compaction: { keepRecentTokens: 5 } });
    if (enabled) await session.prompt("next turn");
    else await assert.rejects(session.prompt("next turn"), /Context is too large/);
    assert.equal(requests.length, enabled ? 4 : 2);
    assert.equal(
      kinds.filter((kind) => kind === "compaction_summary").length,
      enabled ? 1 : 0,
    );
    assert.equal(JSON.stringify(session.history).includes("retry result"), enabled);
    assert.ok(!(await disk(session)).includes("exceeds the context window"));
  });
}

test("system rebuilds remain private while user admission is pending or denied", async () => {
  let release!: () => void;
  let reached!: () => void;
  const hold = new Promise<void>((resolve) => {
    release = resolve;
  });
  const seen = new Promise<void>((resolve) => {
    reached = resolve;
  });
  let denyUser = true;
  const { session, requests } = await fixture(async (kind, body) => {
    if (kind === "system_context") return {
      ...allow,
      decision: "replace",
      replacement: { ...body, text: "APPROVED_SYSTEM" },
    };
    if (kind === "user_message" && denyUser) {
      reached();
      await hold;
      return deny;
    }
    return kind === "provider_context" ? { ...allow, receipt: "receipt" } : allow;
  });
  assert.equal(session.systemPrompt, "APPROVED_SYSTEM");
  const running = session.prompt("denied");
  const rejected = assert.rejects(running);
  await seen;
  assert.equal(session.agent.state.systemPrompt, "APPROVED_SYSTEM");
  release();
  await rejected;
  assert.equal(session.systemPrompt, "APPROVED_SYSTEM");
  assert.equal(session.messages.length, 0);
  denyUser = false;
  await session.prompt("allowed");
  assert.equal(requests[0].systemPrompt, "APPROVED_SYSTEM");
  assert.equal(session.systemPrompt, "APPROVED_SYSTEM");
});

test("native session persists each approved message once and never renders tool details", async () => {
  const { session, cwd } = await fixture(undefined, [
    answer("", [
      { id: "read", name: "read", arguments: { path: "notes.txt" } },
    ]),
    answer("Done"),
  ]);
  await writeFile(join(cwd, "notes.txt"), "Approved file");
  const tool = session.agent.state.tools.find((tool) => tool.name === "read")!;
  const execute = tool.execute;
  tool.execute = async (id, args, signal, onUpdate) => {
    onUpdate?.({
      content: [{ type: "text", text: "UNCHECKED_PROGRESS" }],
      details: undefined,
    });
    const result = await execute(id, args, signal);
    return { ...result, details: { diff: "UNCHECKED_DETAILS" } };
  };
  const events: unknown[] = [];
  session.subscribe((event) => {
    events.push(structuredClone(event));
  });
  await session.prompt("Read notes.txt");
  assert.equal(session.messages.length, 4);
  assert.equal(
    session.entries.filter((entry) => entry.type === "message").length,
    4,
  );
  const saved = (await disk(session))
    .trim()
    .split("\n")
    .map((line) => JSON.parse(line));
  assert.equal(saved.filter((entry) => entry.type === "message").length, 4);
  assert.ok(JSON.stringify(events).includes("Approved file"));
  for (const snapshot of [
    JSON.stringify(events),
    JSON.stringify(session.messages),
    await disk(session),
  ])
    assert.ok(!snapshot.includes("UNCHECKED"));
});

for (const mode of ["steer", "followUp"] as const) {
  test(`native ${mode} queue admits expanded input before transcript insertion`, async () => {
    let release!: () => void;
    let reached!: () => void;
    const seen = new Promise<void>((resolve) => {
      reached = resolve;
    });
    const hold = new Promise<void>((resolve) => {
      release = resolve;
    });
    const { session, requests } = await fixture(
      async (kind, body) => {
        if (kind === "assistant_message" && body.text === "First") {
          reached();
          await hold;
        }
        if (
          kind === "user_message" &&
          String(body.text).includes("SKILL_CANDIDATE")
        )
          return {
            ...allow,
            decision: "replace",
            replacement: {
              ...body,
              text: String(body.text).replace(
                "SKILL_CANDIDATE",
                "APPROVED_SKILL",
              ),
            },
          };
        return kind === "provider_context"
          ? { ...allow, receipt: "receipt" }
          : allow;
      },
      [answer("First"), answer("Second")],
    );
    const running = session.prompt("hello");
    await seen;
    await session[mode]("/skill:example");
    assert.ok(!JSON.stringify(session.messages).includes("SKILL_CANDIDATE"));
    assert.ok(!(await disk(session)).includes("SKILL_CANDIDATE"));
    release();
    await running;
    assert.equal(requests.length, 2);
    assert.ok(JSON.stringify(requests[1]).includes("APPROVED_SKILL"));
    assert.ok(!JSON.stringify(session.entries).includes("SKILL_CANDIDATE"));
    assert.equal(session.agent.hasQueuedMessages(), false);
    assert.equal(
      session.getSteeringMessages().length +
        session.getFollowUpMessages().length,
      0,
    );
  });
}

test("abort settles the native lifecycle without saving late content and permits the next prompt", async () => {
  let release!: () => void;
  let reached!: () => void;
  const seen = new Promise<void>((resolve) => {
    reached = resolve;
  });
  const hold = new Promise<void>((resolve) => {
    release = resolve;
  });
  const { session } = await fixture(
    async (kind, body) => {
      if (kind === "assistant_message" && body.text === "LATE_RESPONSE") {
        reached();
        await hold;
      }
      return kind === "provider_context"
        ? { ...allow, receipt: "receipt" }
        : allow;
    },
    [answer("LATE_RESPONSE"), answer("Next reply")],
  );
  const running = session.prompt("first");
  await seen;
  const rejected = assert.rejects(running);
  const abort = session.abort();
  assert.equal(session.agent.state.isStreaming, true);
  release();
  await Promise.all([rejected, abort]);
  await session.agent.waitForIdle();
  assert.equal(session.isStreaming, false);
  assert.ok(!JSON.stringify(session.messages).includes("LATE_RESPONSE"));
  assert.ok(!(await disk(session)).includes("LATE_RESPONSE"));
  await session.prompt("next");
  assert.ok(JSON.stringify(session.history).includes("Next reply"));
});

test("native alternate writes fail closed; /new reuses the admission factory", async () => {
  const cwd = await mkdtemp(join(tmpdir(), "pi-runtime-test-"));
  const runtime = await createAdmissionRuntime({
    cwd,
    agentDir: join(cwd, "agent"),
    sessionDir: join(cwd, "sessions"),
    model,
    apiKey: "placeholder",
    admission: new Admission(async (kind) =>
      kind === "user_message" ? deny : allow,
    ),
  });
  await runtime.session.bindExtensions({});
  const before = JSON.stringify(runtime.session.messages);
  const entries = runtime.session.sessionManager.getEntries();
  await assert.rejects(
    runtime.session.executeBash("touch must-not-exist"),
    /not supported/,
  );
  assert.throws(
    () =>
      runtime.session.recordBashResult("bad", {
        output: "UNCHECKED",
        exitCode: 0,
        cancelled: false,
        truncated: false,
      }),
    /not supported/,
  );
  await assert.rejects(
    runtime.session.sendCustomMessage({
      customType: "test",
      content: "UNCHECKED",
      display: true,
    }),
    /not supported/,
  );
  await assert.rejects(
    runtime.session.navigateTree("missing"),
    /not supported/,
  );
  await assert.rejects(runtime.session.setModel(model), /not supported/);
  assert.throws(
    () => runtime.session.setSessionName("UNCHECKED"),
    /not supported/,
  );
  await assert.rejects(
    runtime.switchSession("/does/not/exist"),
    /not supported/,
  );
  await assert.rejects(
    runtime.importFromJsonl("/does/not/exist"),
    /not supported/,
  );
  await assert.rejects(runtime.fork("missing"), /not supported/);
  assert.equal(JSON.stringify(runtime.session.messages), before);
  assert.deepEqual(runtime.session.sessionManager.getEntries(), entries);
  await assert.rejects(readFile(join(cwd, "must-not-exist")));
  const previous = runtime.session;
  previous.setAutoCompactionEnabled(false);
  previous.setSteeringMode("all");
  previous.setFollowUpMode("all");
  assert.equal(previous.agent.sessionId, previous.sessionId);
  await runtime.newSession();
  assert.notEqual(runtime.session, previous);
  assert.equal(runtime.session.autoCompactionEnabled, false);
  assert.equal(runtime.session.steeringMode, "all");
  assert.equal(runtime.session.followUpMode, "all");
  assert.notEqual(runtime.session.sessionId, previous.sessionId);
  assert.equal(runtime.session.agent.sessionId, runtime.session.sessionId);
  await runtime.session.bindExtensions({});
  await assert.rejects(runtime.session.prompt("DENIED"));
  assert.equal(runtime.session.messages.length, 0);
  assert.equal(runtime.session.sessionManager.getEntries().length, 0);
  await runtime.dispose();
});
