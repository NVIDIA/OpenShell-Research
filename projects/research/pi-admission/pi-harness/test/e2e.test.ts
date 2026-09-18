// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import assert from "node:assert/strict";
import { mkdtemp, readFile } from "node:fs/promises";
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
  type AdmissionResponse,
  type Evaluate,
} from "../src/admission.js";
import { AdmissionSession } from "../src/session.js";

const model: Model<"openai-completions"> = {
  id: "test",
  name: "Test",
  provider: "test",
  api: "openai-completions",
  baseUrl: "https://provider.test/v1",
  reasoning: true,
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

function answer(text: string): AssistantMessage {
  return {
    role: "assistant",
    content: [{ type: "text", text }],
    api: model.api,
    model: model.id,
    provider: model.provider,
    timestamp: Date.now(),
    stopReason: "stop",
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

function writeCall(): AssistantMessage {
  return {
    ...answer(""),
    content: [
      { type: "thinking", thinking: "Use the native write tool." },
      {
        type: "toolCall",
        id: "write-1",
        name: "write",
        arguments: { path: "tool-output.txt", content: "written by Pi" },
        thoughtSignature: "opaque-replay-data",
      },
    ],
    stopReason: "toolUse",
  };
}

async function fixture(evaluate: Evaluate) {
  const cwd = await mkdtemp(join(tmpdir(), "pi-admission-e2e-"));
  const requests: Context[] = [];
  const stream: StreamFn = (_model, context, options) => {
    assert.equal(options?.headers?.["x-pi-admission-receipt"], "receipt");
    assert.equal(options?.reasoning, "medium");
    requests.push(structuredClone({ ...context, tools: undefined }));
    const result = createAssistantMessageEventStream();
    const message = requests.length === 1 ? writeCall() : answer("Done");
    result.push({
      type: "done",
      reason: message.stopReason === "toolUse" ? "toolUse" : "stop",
      message,
    });
    return result;
  };
  const session = await AdmissionSession.create({
    cwd,
    sessionDir: join(cwd, "sessions"),
    agentDir: join(cwd, "agent"),
    model,
    apiKey: "placeholder",
    admission: new Admission(evaluate),
    stream,
  });
  return { cwd, session, requests };
}

async function saved(session: AdmissionSession): Promise<string> {
  try {
    return await readFile(session.sessionFile, "utf8");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return "";
    throw error;
  }
}

test("allowed and redacted input reaches the provider and saved history with a receipt", async () => {
  const kinds: string[] = [];
  let releaseTool!: () => void;
  let toolPending!: () => void;
  const toolGate = new Promise<void>((resolve) => (releaseTool = resolve));
  const pending = new Promise<void>((resolve) => (toolPending = resolve));
  const { cwd, session, requests } = await fixture(async (kind, body) => {
    kinds.push(kind);
    if (kind === "provider_context") return { ...allow, receipt: "receipt" };
    if (kind === "tool_result") {
      toolPending();
      await toolGate;
    }
    if (kind === "user_message" && body.text === "alice@example.com") {
      return {
        decision: "replace",
        replacement: { ...body, text: "[EMAIL]" },
        receipt: null,
      };
    }
    return allow;
  });

  assert.deepEqual(session.getActiveToolNames(), ["read", "bash", "edit", "write"]);
  const firstTurn = session.prompt("plain text");
  await pending;
  assert.equal(session.history.some((message) => message.role === "assistant"), false);
  releaseTool();
  await firstTurn;
  await session.prompt("alice@example.com");

  assert.equal(requests.length, 3);
  assert.ok(kinds.includes("tool_result"));
  assert.equal(
    await readFile(join(cwd, "tool-output.txt"), "utf8"),
    "written by Pi",
  );
  const snapshots = [
    JSON.stringify(requests),
    JSON.stringify(session.history),
    await saved(session),
  ];
  assert.ok(
    snapshots.every((snapshot) => snapshot.includes("Successfully wrote")),
  );
  assert.ok(snapshots.every((snapshot) => snapshot.includes("plain text")));
  assert.ok(snapshots.every((snapshot) => snapshot.includes("opaque-replay-data")));
  assert.ok(snapshots.every((snapshot) => snapshot.includes("[EMAIL]")));
  assert.ok(
    snapshots.every((snapshot) => !snapshot.includes("alice@example.com")),
  );

  session.settingsManager.applyOverrides({ compaction: { keepRecentTokens: 1 } });
  assert.ok(await session.compact());
  assert.ok(kinds.includes("compaction_summary"));
  const compaction = session.entries.find((entry) => entry.type === "compaction");
  assert.ok(
    compaction && (!("details" in compaction) || compaction.details === undefined),
  );
});

test("denied input never reaches the provider, live history, or saved history", async () => {
  const forbidden = "123-45-6789";
  const { session, requests } = await fixture(async (kind) =>
    kind === "user_message"
      ? { decision: "deny", replacement: null, receipt: null }
      : allow,
  );

  await assert.rejects(session.prompt(forbidden));

  assert.deepEqual(requests, []);
  assert.ok(!JSON.stringify(session.history).includes(forbidden));
  assert.ok(!JSON.stringify(session.entries).includes(forbidden));
  assert.ok(!(await saved(session)).includes(forbidden));

  let release!: () => void;
  let admissionPending!: () => void;
  const gate = new Promise<void>((resolve) => (release = resolve));
  const pending = new Promise<void>((resolve) => (admissionPending = resolve));
  const cancelled = await fixture(async (kind) => {
    if (kind === "user_message") {
      admissionPending();
      await gate;
    }
    return kind === "provider_context" ? { ...allow, receipt: "receipt" } : allow;
  });
  const turn = cancelled.session.prompt("cancel me");
  await pending;
  await assert.rejects(cancelled.session.prompt("busy"), /busy/);
  const abort = cancelled.session.abort();
  release();
  await abort;
  await assert.rejects(turn);
  assert.equal(JSON.stringify(cancelled.session.history).includes("cancel me"), false);
});
