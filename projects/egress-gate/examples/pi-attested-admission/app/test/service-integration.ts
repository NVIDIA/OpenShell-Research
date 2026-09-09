// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

// Invoked by pytest with real local admission/provider endpoints. No stream or
// evaluator is replaced: Pi serializes requests and consumes the provider SSE.
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import type { Model } from "@earendil-works/pi-ai";
import {
  Admission,
  AdmissionError,
  createHttpEvaluator,
} from "../src/admission.js";
import { AdmissionSession } from "../src/session.js";

const [endpoint, directory] = process.argv.slice(2);
const model: Model<"openai-completions"> = {
  id: "test",
  name: "Local integration provider",
  provider: "test",
  api: "openai-completions",
  baseUrl: `${endpoint}/v1`,
  reasoning: false,
  input: ["text"],
  contextWindow: 100000,
  maxTokens: 4096,
  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
  compat: { maxTokensField: "max_tokens", supportsDeveloperRole: false },
};
const create = (compactAtTokens?: number) =>
  AdmissionSession.create({
    cwd: join(directory, "image/project"),
    sessionDir: join(directory, "sessions"),
    agentDir: join(directory, "agent"),
    model,
    apiKey: "local-test-credential",
    admission: new Admission(
      createHttpEvaluator(
        `${endpoint}/v1/admission`,
        "test-admission-credential",
        randomUUID(),
      ),
    ),
    compactAtTokens,
  });

const session = await create();
await assert.rejects(session.prompt("DENY_THIS"), AdmissionError);
assert.equal(session.history.length, 0);
assert.equal(session.entries.length, 0);
await session.prompt("Please repeat REDACT_THIS and café.");
await session.prompt("/skill:review");
const readResult = session.history.find(
  (message) => message.role === "toolResult" && message.toolName === "read",
);
assert.ok(readResult?.role === "toolResult");
assert.equal(readResult.isError, false);
assert.match(
  JSON.stringify(readResult.content),
  /This is a real file in the sandbox project\./,
);
assert.match(JSON.stringify(readResult.content), /\[REDACTED\]/);
for (const snapshot of [
  JSON.stringify(session.history),
  await readFile(session.sessionFile, "utf8"),
]) {
  assert.ok(snapshot.includes("[REDACTED]"));
  assert.ok(!snapshot.includes("REDACT_THIS") && !snapshot.includes("DENY_THIS"));
}
assert.ok(await session.compact());
assert.ok(session.entries.some((entry) => entry.type === "compaction"));
assert.match(JSON.stringify(session.history), /Approved summary/);

const automatic = await create(1);
await automatic.prompt("Hello");
await automatic.prompt("One more turn");
assert.ok(automatic.entries.some((entry) => entry.type === "compaction"));
for (const current of [session, automatic]) {
  for (const snapshot of [
    JSON.stringify(current.history),
    JSON.stringify(current.entries),
    await readFile(current.sessionFile, "utf8"),
  ]) {
    assert.ok(!snapshot.includes("REDACT_THIS") && !snapshot.includes("DENY_THIS"));
  }
}
