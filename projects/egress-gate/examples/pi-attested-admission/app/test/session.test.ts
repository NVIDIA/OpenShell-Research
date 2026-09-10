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

test("redacted real tool output is the only version saved and sent on continuation", async () => {
  const { session, cwd, requests } = await fixture(
    async (kind, body) => {
      if (kind === "tool_result")
        return {
          decision: "replace",
          replacement: {
            ...body,
            content: [{ type: "text", text: "[REDACTED]" }],
          },
          receipt: null,
        };
      return kind === "provider_context"
        ? { ...allow, receipt: "receipt" }
        : allow;
    },
    [
      answer("", [
        { id: "call", name: "read", arguments: { path: "candidate.txt" } },
      ]),
      answer("Finished"),
    ],
  );
  await writeFile(join(cwd, "candidate.txt"), "RAW_TOOL_CONTENT");
  await session.prompt("Read candidate.txt");
  assert.equal(requests.length, 2);
  assert.ok(JSON.stringify(requests[1]).includes("[REDACTED]"));
  assert.ok(!JSON.stringify(session.entries).includes("RAW_TOOL_CONTENT"));
  assert.ok(!(await disk(session)).includes("RAW_TOOL_CONTENT"));
  assert.ok((await disk(session)).includes("[REDACTED]"));
});

test("manual compaction waits for approval and preserves the latest whole turn", async () => {
  let release!: (result: AdmissionResponse) => void;
  let reached!: () => void;
  const seen = new Promise<void>((resolve) => {
    reached = resolve;
  });
  const pending = new Promise<AdmissionResponse>((resolve) => {
    release = resolve;
  });
  let summaryBody: Record<string, unknown> = {};
  const { session, requests } = await fixture(
    async (kind, body) => {
      if (kind === "compaction_summary") {
        summaryBody = body;
        reached();
        return pending;
      }
      return kind === "provider_context"
        ? { ...allow, receipt: "receipt" }
        : allow;
    },
    [answer("first"), answer("second"), answer("SUMMARY_CANDIDATE")],
  );
  await session.prompt("first turn");
  await session.prompt("second turn");
  const before = session.entries;
  const fileBefore = await disk(session);
  const compact = session.compact();
  await seen;
  assert.deepEqual(session.entries, before);
  assert.equal(await disk(session), fileBefore);
  release({
    decision: "replace",
    replacement: { ...summaryBody, text: "Approved summary" },
    receipt: null,
  });
  assert.equal((await compact).summary, "Approved summary");
  assert.equal(requests.length, 3);
  assert.ok(!JSON.stringify(session.history).includes("SUMMARY_CANDIDATE"));
  assert.ok(JSON.stringify(session.history).includes("Approved summary"));
  assert.ok(JSON.stringify(session.history).includes("second turn"));
  assert.ok(!(await disk(session)).includes("SUMMARY_CANDIDATE"));
  assert.ok(
    (await disk(session)).includes("first turn"),
    "compaction is append-only",
  );
});

test("automatic compaction uses the same admitted summary path", async () => {
  const { session, kinds } = await fixture(
    undefined,
    [answer("first"), answer("second"), answer("summary")],
    1,
  );
  await session.prompt("one");
  await session.prompt("two");
  assert.equal(kinds.filter((kind) => kind === "compaction_summary").length, 1);
  assert.equal(
    session.entries.filter((entry) => entry.type === "compaction").length,
    1,
  );
});

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

test("denied summary leaves both histories unchanged", async () => {
  const { session } = await fixture(
    async (kind) =>
      kind === "compaction_summary"
        ? deny
        : kind === "provider_context"
          ? { ...allow, receipt: "receipt" }
          : allow,
    [answer("first"), answer("second"), answer("UNAPPROVED_SUMMARY")],
  );
  await session.prompt("first turn");
  await session.prompt("second turn");
  const before = session.entries;
  const saved = await disk(session);
  await assert.rejects(session.compact());
  assert.deepEqual(session.entries, before);
  assert.equal(await disk(session), saved);
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

test("context overflow makes one admitted summary and one retry", async () => {
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
  await session.prompt("next turn");
  assert.equal(requests.length, 4);
  assert.equal(kinds.filter((kind) => kind === "compaction_summary").length, 1);
  assert.ok(JSON.stringify(session.history).includes("retry result"));
  assert.ok(!(await disk(session)).includes("exceeds the context window"));
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
  await runtime.newSession();
  assert.notEqual(runtime.session, previous);
  await runtime.session.bindExtensions({});
  await assert.rejects(runtime.session.prompt("DENIED"));
  assert.equal(runtime.session.messages.length, 0);
  assert.equal(runtime.session.sessionManager.getEntries().length, 0);
  await runtime.dispose();
});
