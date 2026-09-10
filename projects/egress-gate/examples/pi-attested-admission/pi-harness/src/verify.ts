// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { parseArgs } from "node:util";
import type { Message } from "@earendil-works/pi-ai";
import { Admission, AdmissionError, createHttpEvaluator } from "./admission.js";
import { AdmissionSession } from "./session.js";
import { configureProxy } from "./network.js";
import { loadSelectedModel } from "./model.js";

export function assertProjectRead(history: readonly Message[]): void {
  assert.ok(
    history.some((message) =>
      message.role === "toolResult" &&
      message.toolName === "read" &&
      !message.isError &&
      message.content.some((block) =>
        block.type === "text" &&
        block.text.includes("This is a real file in the sandbox project.") &&
        block.text.includes("[REDACTED]"),
      ),
    ),
    "The read tool must successfully return the approved notes.txt content",
  );
}

/** Real service, upstream runtime, real project tools, and the configured model. */
async function verify(): Promise<void> {
  configureProxy();
  const { values } = parseArgs({ options: { admission: { type: "string" } } });
  const apiKey = process.env.PI_MODEL_API_KEY;
  const admissionKey = process.env.EGRESS_ADMISSION_TOKEN;
  delete process.env.PI_MODEL_API_KEY;
  delete process.env.EGRESS_ADMISSION_TOKEN;
  assert.ok(
    apiKey && admissionKey && values.admission,
    "Missing example configuration",
  );
  const model = await loadSelectedModel();
  const makeSession = (compactAtTokens?: number) =>
    AdmissionSession.create({
      cwd: "/sandbox/project",
      sessionDir: "/sandbox/sessions",
      agentDir: "/app/agent",
      model,
      apiKey,
      compactAtTokens,
      admission: new Admission(
        createHttpEvaluator(values.admission!, admissionKey, randomUUID()),
      ),
    });
  const raw = await fetch(`${model.baseUrl}/chat/completions`, {
    method: "POST",
    headers: {
      authorization: `Bearer ${apiKey}`,
      "content-type": "application/json",
    },
    body: JSON.stringify({
      model: model.id,
      messages: [{ role: "user", content: "Harmless bypass check" }],
    }),
    signal: AbortSignal.timeout(30_000),
  });
  assert.equal(
    raw.status,
    403,
    "A request without an approval receipt must be blocked",
  );
  console.log("PASS raw provider request without receipt is blocked");
  const session = await makeSession();
  await assert.rejects(
    session.prompt("DENY_THIS"),
    (error) => error instanceof AdmissionError && error.kind === "denied",
  );
  assert.deepEqual(session.entries, []);
  console.log(
    "PASS denied user input is absent from live history and Pi entries",
  );
  await session.prompt("Reply briefly to this harmless text: REDACT_THIS");
  await session.prompt(
    "/skill:review Use the read tool to read notes.txt; do not guess its contents.",
  );
  assertProjectRead(session.history);
  assert.ok(
    await session.compact(),
    "Manual compaction must summarize an older turn",
  );
  const saved = await readFile(session.sessionFile, "utf8");
  for (const snapshot of [
    JSON.stringify(session.history),
    JSON.stringify(session.entries),
    saved,
  ]) {
    assert.ok(
      !snapshot.includes("DENY_THIS") && !snapshot.includes("REDACT_THIS"),
    );
    assert.ok(snapshot.includes("[REDACTED]"));
  }
  console.log(
    "PASS real model, redacted input, rendered skill, tool continuation, manual compaction, and JSONL history",
  );
  const automatic = await makeSession(1);
  await automatic.prompt("Reply with a brief greeting.");
  await automatic.prompt("Reply with a brief farewell.");
  assert.ok(automatic.entries.some((entry) => entry.type === "compaction"));
  console.log("PASS automatic compaction through the same admission boundary");
  console.log(
    `Saved evidence: ${session.sessionFile}\n${automatic.sessionFile}`,
  );
}

if (import.meta.main) {
  verify().catch(() => {
    console.error(
      "FAIL end-to-end verification. Check service availability, credentials, model compatibility, and the last PASS line; no checks were skipped.",
    );
    process.exitCode = 1;
  });
}
