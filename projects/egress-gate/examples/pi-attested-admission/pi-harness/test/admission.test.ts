// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import assert from "node:assert/strict";
import { test } from "node:test";
import type { AssistantMessage } from "@earendil-works/pi-ai";
import {
  Admission,
  AdmissionError,
  createHttpEvaluator,
  textOnly,
} from "../src/admission.js";

test("allow preserves native messages; executable and signed reasoning changes fail closed", async () => {
  assert.throws(() => textOnly([{ type: "image" }]), AdmissionError);
  const message: AssistantMessage = {
    role: "assistant",
    api: "openai-completions",
    model: "test",
    provider: "test",
    timestamp: 0,
    stopReason: "toolUse",
    content: [
      { type: "text", text: "before" },
      { type: "thinking", thinking: "private reasoning", thinkingSignature: "provider-signature" },
      {
        type: "toolCall",
        id: "call",
        name: "bash",
        arguments: { command: "original" },
        thoughtSignature: "tool-signature",
      },
      { type: "text", text: "after", textSignature: "text-signature" },
    ],
    usage: {
      input: 0,
      output: 0,
      cacheRead: 0,
      cacheWrite: 0,
      totalTokens: 0,
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
    },
  };
  const allowed = new Admission(async () => ({ decision: "allow", replacement: null, receipt: null }));
  assert.strictEqual(await allowed.message(message), message);
  for (const role of ["user", "toolResult"] as const) {
    const candidate = { role, content: [{ type: "text" as const, text: "one" }, { type: "text" as const, text: "two" }],
      timestamp: 1, toolCallId: "call", toolName: "read", isError: false };
    assert.strictEqual(await allowed.message(candidate), candidate);
  }
  const signedChange = new Admission(async (_kind, body) => ({
    decision: "replace", receipt: null,
    replacement: { ...body, thinking: [{ text: "changed", signature: "provider-signature" }] },
  }));
  await assert.rejects(signedChange.message(message), AdmissionError);
  const unsigned = { ...message, content: [{ type: "thinking" as const, thinking: "private reasoning" }] };
  const redactor = new Admission(async (_kind, body) => ({
    decision: "replace", receipt: null,
    replacement: { ...body, thinking: [{ text: "approved reasoning", signature: null }] },
  }));
  assert.deepEqual((await redactor.message(unsigned)).content, [{ type: "thinking", thinking: "approved reasoning" }]);
  const admission = new Admission(async (_kind, body) => ({
    decision: "replace",
    replacement: { ...body, tool_calls: [] },
    receipt: null,
  }));
  await assert.rejects(admission.message(message), AdmissionError);
});

test("send-only redaction is rejected, and receipt projection preserves user/tool order", async () => {
  const admission = new Admission(async (_kind, body) => {
    assert.deepEqual(body.entries, [
      { role: "user", text: "hello" },
      { role: "tool", tool_call_id: "call", text: "(no tool output)" },
    ]);
    return { decision: "replace", replacement: body, receipt: "receipt" };
  });
  await assert.rejects(
    admission.receipt({
      messages: [
        { role: "user", content: "hello", timestamp: 0 },
        {
          role: "toolResult",
          toolCallId: "call|provider-suffix",
          toolName: "read",
          content: [],
          isError: false,
          timestamp: 0,
        },
      ],
    }),
    AdmissionError,
  );
});

test("HTTP client rejects insecure configuration and malformed service output", async () => {
  assert.throws(() =>
    createHttpEvaluator("http://service.test", "placeholder", "session"),
  );
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async () =>
      new Response(
        JSON.stringify({
          decision: "allow",
          replacement: { text: "unchecked" },
          receipt: null,
        }),
      );
    await assert.rejects(
      createHttpEvaluator(
        "https://service.test",
        "placeholder",
        "session",
      )("user_message", {}),
      AdmissionError,
    );
    globalThis.fetch = async () => {
      throw new Error("RAW_SECRET");
    };
    await assert.rejects(
      createHttpEvaluator(
        "https://service.test",
        "placeholder",
        "session",
      )("user_message", {}),
      (error) =>
        error instanceof AdmissionError &&
        !error.message.includes("RAW_SECRET"),
    );
  } finally {
    globalThis.fetch = original;
  }
});
