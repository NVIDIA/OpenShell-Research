// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { AdmissionSession } from "./session.js";
import { loadSessionOptions } from "./config.js";

async function verify(): Promise<void> {
  const { mode, session: options } = await loadSessionOptions();
  const session = await AdmissionSession.create(options);
  const syntheticKey = `sk-${mode}_${randomUUID().replaceAll("-", "_")}`;

  console.log(`Local admission: ${mode}`);
  console.log(`Transcript: ${session.sessionFile}`);
  await session.prompt(
    `Acknowledge only; do not use tools. Synthetic key: ${syntheticKey}`,
  );
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
    if (mode === "on") {
      assert.ok(snapshot.includes("[REDACTED]"));
      assert.ok(!snapshot.includes(syntheticKey));
    } else {
      assert.ok(snapshot.includes(syntheticKey));
    }
  }
  console.log(`PASS ${mode} mode persisted the expected user-message value`);
  console.log("PASS native write tool completed through the controlled loop");

  session.settingsManager.applyOverrides({ compaction: { keepRecentTokens: 1 } });
  assert.ok(await session.compact(), "Compaction must use the admission boundary");
  if (mode === "on")
    assert.ok(!(await readFile(session.sessionFile, "utf8")).includes(syntheticKey));
  console.log("PASS manual compaction completed through local admission");
}

if (import.meta.main) {
  verify().catch((error: unknown) => {
    console.error(error);
    console.error(
      "FAIL live verification. Check the gateway, model credential, model compatibility, and the last PASS line.",
    );
    process.exitCode = 1;
  });
}
