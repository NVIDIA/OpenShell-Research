// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import assert from "node:assert/strict";
import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import type { StreamFn } from "@earendil-works/pi-agent-core";
import {
  createAssistantMessageEventStream,
  type AssistantMessage,
  type Context,
  type Model,
} from "@earendil-works/pi-ai";
import { Admission, AdmissionError, type AdmissionMode } from "../src/admission.js";
import { AdmissionSession, createAdmissionRuntime } from "../src/session.js";

const syntheticKey = "sk-LOCAL_TEST_ONLY_123456789";
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
      {
        type: "thinking",
        thinking: `Use the native write tool. ${syntheticKey}`,
        thinkingSignature: "reasoning_content",
      },
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

function multiBlockAnswer(): AssistantMessage {
  return {
    ...answer(""),
    content: [
      { type: "text", text: "assistant output" },
      { type: "text", text: syntheticKey },
    ],
  };
}

function bashCall(): AssistantMessage {
  return {
    ...answer(""),
    content: [
      {
        type: "toolCall",
        id: "bash-1",
        name: "bash",
        // The unmodified call constructs the value only at execution time. Its
        // result then exercises the tool-result admission boundary.
        arguments: {
          command: "printf 'sk-%s%s\\n' 'LOCAL_TEST_' 'ONLY_123456789'",
        },
      },
    ],
    stopReason: "toolUse",
  };
}

function result(message: AssistantMessage) {
  const stream = createAssistantMessageEventStream();
  stream.push({
    type: "done",
    reason: message.stopReason === "toolUse" ? "toolUse" : "stop",
    message,
  });
  return stream;
}

async function fixture(mode: AdmissionMode, stream?: StreamFn) {
  const cwd = await mkdtemp(join(tmpdir(), "pi-admission-e2e-"));
  const requests: Context[] = [];
  const defaultStream: StreamFn = (_model, context, options) => {
    assert.equal(options?.reasoning, "medium");
    assert.equal(options?.apiKey, "placeholder");
    requests.push(structuredClone({ ...context, tools: undefined }));
    const message =
      requests.length === 1
        ? writeCall()
        : requests.length === 2
          ? bashCall()
          : requests.length === 3
            ? multiBlockAnswer()
            : answer(`compaction summary ${syntheticKey}`);
    return result(message);
  };
  const runtime = await createAdmissionRuntime({
    cwd,
    sessionDir: join(cwd, "sessions"),
    agentDir: join(cwd, "agent"),
    model,
    apiKey: "placeholder",
    admission: new Admission(mode),
    stream: stream ?? defaultStream,
  });
  const session = runtime.session;
  assert.ok(session instanceof AdmissionSession);
  await session.bindExtensions({});
  return { cwd, session, requests, runtime };
}

async function saved(session: AdmissionSession): Promise<string> {
  try {
    return await readFile(session.sessionFile, "utf8");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return "";
    throw error;
  }
}

for (const mode of ["off", "on"] as const) {
  test(`admission ${mode} controls every supported publication boundary`, async () => {
    const { cwd, session, requests, runtime } = await fixture(mode);
    for (const change of [
      () => runtime.newSession(),
      () => runtime.switchSession("unchecked.jsonl"),
      () => runtime.importFromJsonl("unchecked.jsonl"),
      () => runtime.fork("unchecked-entry"),
    ])
      assert.deepEqual(await change(), { cancelled: true });

    assert.deepEqual(session.getActiveToolNames(), ["read", "bash", "edit", "write"]);
    await session.prompt(`user input ${syntheticKey}`);
    assert.equal(await readFile(join(cwd, "tool-output.txt"), "utf8"), "written by Pi");
    assert.equal(requests.length, 3);
    const finalAssistant = session.history.at(-1);
    assert.equal(finalAssistant?.role, "assistant");
    assert.equal(finalAssistant?.content.length, 2);

    const snapshots = [
      JSON.stringify(requests),
      JSON.stringify(session.history),
      await saved(session),
    ];
    assert.ok(snapshots.every((snapshot) => snapshot.includes("opaque-replay-data")));
    assert.ok(snapshots.every((snapshot) => snapshot.includes("reasoning_content")));
    assert.ok(snapshots.every((snapshot) => snapshot.includes("Successfully wrote")));
    if (mode === "on") {
      assert.ok(snapshots.every((snapshot) => snapshot.includes("[REDACTED]")));
      assert.ok(snapshots.every((snapshot) => !snapshot.includes(syntheticKey)));
    } else {
      assert.ok(snapshots.every((snapshot) => snapshot.includes(syntheticKey)));
    }

    session.settingsManager.applyOverrides({ compaction: { keepRecentTokens: 1 } });
    assert.ok(await session.compact());
    const compaction = session.entries.find((entry) => entry.type === "compaction");
    assert.ok(compaction && (!('details' in compaction) || compaction.details === undefined));
    const summary = JSON.stringify(compaction);
    if (mode === "on") {
      assert.ok(summary.includes("[REDACTED]"));
      assert.ok(!summary.includes(syntheticKey));
    } else {
      assert.ok(summary.includes(syntheticKey));
    }
  });
}

test("signed transformations are rejected before assistant publication", async () => {
  const signed = answer(syntheticKey);
  signed.content = [{ type: "text", text: syntheticKey, textSignature: "signed" }];
  const { session } = await fixture("on", () => result(signed));

  await assert.rejects(
    session.prompt("safe user text"),
    (error) => error instanceof AdmissionError && error.kind === "invalid",
  );
  assert.equal(session.history.some((message) => message.role === "assistant"), false);
  assert.ok(!(await saved(session)).includes(syntheticKey));

  const opaqueReasoning = answer("");
  opaqueReasoning.content = [
    {
      type: "thinking",
      thinking: syntheticKey,
      thinkingSignature: "opaque-reasoning-signature",
    },
  ];
  await assert.rejects(
    new Admission("on").message(opaqueReasoning),
    (error) => error instanceof AdmissionError && error.kind === "invalid",
  );
});

test("tool calls that would require semantic rewriting are rejected", async () => {
  const call = writeCall();
  const toolCall = call.content.find((block) => block.type === "toolCall");
  assert.ok(toolCall?.type === "toolCall");
  toolCall.arguments = { path: "unsafe.txt", content: syntheticKey };
  const { cwd, session } = await fixture("on", () => result(call));

  await assert.rejects(
    session.prompt("safe user text"),
    (error) => error instanceof AdmissionError && error.kind === "invalid",
  );
  await assert.rejects(readFile(join(cwd, "unsafe.txt")), { code: "ENOENT" });
  assert.equal(session.history.some((message) => message.role === "assistant"), false);
});

test("cancellation does not publish an incomplete assistant response", async () => {
  let release!: () => void;
  let started!: () => void;
  const gate = new Promise<void>((resolve) => (release = resolve));
  const pending = new Promise<void>((resolve) => (started = resolve));
  const stream: StreamFn = () => {
    const events = createAssistantMessageEventStream();
    started();
    void gate.then(() => events.push({ type: "done", reason: "stop", message: answer("late") }));
    return events;
  };
  const { session } = await fixture("on", stream);
  const turn = session.prompt("checked user text");
  await pending;
  await assert.rejects(session.prompt("busy"), /busy/);
  const abort = session.abort();
  release();
  await abort;
  await assert.rejects(turn);
  assert.equal(session.history.some((message) => message.role === "assistant"), false);
  assert.ok(!(await saved(session)).includes("late"));
});
