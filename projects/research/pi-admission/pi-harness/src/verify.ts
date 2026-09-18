// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { parseArgs } from "node:util";
import { Admission, AdmissionError, createHttpEvaluator } from "./admission.js";
import { AdmissionSession } from "./session.js";
import { configureProxy } from "./network.js";
import { loadSelectedModel } from "./model.js";

async function verify(): Promise<void> {
  configureProxy();
  const { values } = parseArgs({ options: { admission: { type: "string" } } });
  const apiKey = process.env.PI_MODEL_API_KEY;
  const admissionKey = process.env.PI_ADMISSION_TOKEN;
  delete process.env.PI_MODEL_API_KEY;
  delete process.env.PI_ADMISSION_TOKEN;
  assert.ok(
    apiKey && admissionKey && values.admission,
    "Missing example configuration",
  );
  const model = await loadSelectedModel();
  const session = await AdmissionSession.create({
    cwd: "/sandbox/workspace",
    sessionDir: "/sandbox/sessions",
    agentDir: "/app/agent",
    model,
    apiKey,
    admission: new Admission(
      createHttpEvaluator(values.admission, admissionKey, randomUUID()),
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
  assert.equal(raw.status, 403, "A request without a receipt must be blocked");
  console.log("PASS request without receipt is blocked");

  await assert.rejects(
    session.prompt("123-45-6789"),
    (error) => error instanceof AdmissionError && error.kind === "denied",
  );
  assert.deepEqual(session.entries, []);
  console.log("PASS denied input is absent from live history");

  await session.prompt("Reply briefly to this harmless text: alice@example.com");
  await session.prompt(
    "Use the write tool to create verify-tool.txt containing only: safe text",
  );
  assert.ok(
    session.history.some(
      (message) =>
        message.role === "toolResult" &&
        message.toolName === "write" &&
        !message.isError,
    ),
    "The model must complete one successful write-tool call",
  );
  const saved = await readFile(session.sessionFile, "utf8");
  for (const snapshot of [JSON.stringify(session.history), saved]) {
    assert.ok(snapshot.includes("[EMAIL]"));
    assert.ok(
      !snapshot.includes("alice@example.com") &&
        !snapshot.includes("123-45-6789"),
    );
  }
  console.log("PASS redaction occurs before live and saved history");
  console.log("PASS a real tool result is admitted before continuation");

  session.settingsManager.applyOverrides({ compaction: { keepRecentTokens: 1 } });
  assert.ok(await session.compact(), "Compaction must use the admission boundary");
  assert.ok(
    !(await readFile(session.sessionFile, "utf8")).includes("alice@example.com"),
  );
  console.log("PASS compaction preserves admitted history");
  console.log(`Saved evidence: ${session.sessionFile}`);
}

if (import.meta.main) {
  verify().catch(() => {
    console.error(
      "FAIL end-to-end verification. Check service availability, credentials, model compatibility, and the last PASS line.",
    );
    process.exitCode = 1;
  });
}
