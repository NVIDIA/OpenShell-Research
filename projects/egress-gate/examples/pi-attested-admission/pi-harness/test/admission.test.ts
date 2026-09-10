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

test("unsupported content and changed executable fields fail closed", async () => {
  assert.throws(() => textOnly([{ type: "image" }]), AdmissionError);
  const message: AssistantMessage = {
    role: "assistant",
    api: "openai-completions",
    model: "test",
    provider: "test",
    timestamp: 0,
    stopReason: "toolUse",
    content: [
      {
        type: "toolCall",
        id: "call",
        name: "bash",
        arguments: { command: "original" },
      },
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
