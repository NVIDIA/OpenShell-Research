// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

// Invoked by pytest with real local admission/provider endpoints. No stream or
// evaluator is replaced: Pi serializes requests and consumes the provider SSE.
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { InteractiveMode } from "@earendil-works/pi-coding-agent";
import {
  Admission,
  AdmissionError,
  createHttpEvaluator,
} from "../src/admission.js";
import { AdmissionSession, createAdmissionRuntime } from "../src/session.js";
import { loadSelectedModel } from "../src/model.js";

const [endpoint, directory] = process.argv.slice(2);
const model = await loadSelectedModel(join(directory, "image"));
assert.equal(model.id, "YOUR_MODEL_ID");
assert.equal(model.compat?.supportsDeveloperRole, false);
// Only the endpoint changes: exercise the prepared catalog through Pi's parser.
model.baseUrl = `${endpoint}/v1`;
const options = (compactAtTokens?: number) => ({
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

if (process.argv.includes("--tui")) {
  const runtime = await createAdmissionRuntime(options());
  assert.equal(runtime.services.modelRuntime.getError(), undefined);
  await new InteractiveMode(runtime, {
    initialMessage: "Please repeat REDACT_THIS and café.",
    initialMessages: ["/skill:review"],
  }).run();
  process.exit(0);
}

const create = (compactAtTokens?: number) =>
  AdmissionSession.create(options(compactAtTokens));

const session = await create();
assert.equal(session.modelRuntime.getError(), undefined);
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
  assert.ok(
    !snapshot.includes("REDACT_THIS") && !snapshot.includes("DENY_THIS"),
  );
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
    assert.ok(
      !snapshot.includes("REDACT_THIS") && !snapshot.includes("DENY_THIS"),
    );
  }
}
