// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { createInterface } from "node:readline/promises";
import { parseArgs } from "node:util";
import type { Model } from "@earendil-works/pi-ai";
import { Admission, AdmissionError, createHttpEvaluator } from "./admission.js";
import { AdmissionSession } from "./session.js";
import { configureProxy } from "./network.js";

async function main(): Promise<void> {
  configureProxy();
  const { values } = parseArgs({
    options: {
      cwd: { type: "string", default: "/sandbox/project" },
      "session-dir": { type: "string", default: "/sandbox/sessions" },
      model: { type: "string", default: "/app/model.json" },
      admission: { type: "string" },
      prompt: { type: "string" },
    },
  });
  const apiKey = process.env.PI_MODEL_API_KEY;
  const admissionKey = process.env.EGRESS_ADMISSION_TOKEN;
  // Retain endpoint-bound placeholders only in the application, not in tool
  // child environments. This is hygiene, not isolation from same-authority code.
  delete process.env.PI_MODEL_API_KEY;
  delete process.env.EGRESS_ADMISSION_TOKEN;
  if (!apiKey || !admissionKey || !values.admission)
    throw new Error("Missing provider or admission configuration.");
  const model = JSON.parse(
    await readFile(values.model, "utf8"),
  ) as Model<"openai-completions">;
  const session = await AdmissionSession.create({
    cwd: values.cwd,
    sessionDir: values["session-dir"],
    agentDir: "/app/agent",
    model,
    apiKey,
    admission: new Admission(
      createHttpEvaluator(values.admission, admissionKey, randomUUID()),
    ),
  });
  console.log(
    `Project: ${resolve(values.cwd)}\nSession: ${session.sessionFile}`,
  );
  if (values.prompt !== undefined) {
    await session.prompt(values.prompt);
    console.log(JSON.stringify(session.history, null, 2));
    return;
  }
  console.log(
    "/skill:<name> [instructions] · /compact · /history · /exit. Ctrl-C cancels the current operation.",
  );
  const terminal = createInterface({
    input: process.stdin,
    output: process.stdout,
  });
  let operation: AbortController | undefined;
  terminal.on("SIGINT", () => {
    if (operation) operation.abort();
    else terminal.close();
  });
  try {
    for await (const line of terminal) {
      if (line === "/exit") break;
      if (!line.trim()) continue;
      operation = new AbortController();
      try {
        if (line === "/history")
          console.log(JSON.stringify(session.history, null, 2));
        else if (line === "/compact")
          console.log(
            (await session.compact(operation.signal))
              ? "Approved summary saved."
              : "No older complete turn to compact.",
          );
        else {
          await session.prompt(line, operation.signal);
          const last = session.history.at(-1);
          if (last?.role === "assistant")
            console.log(
              last.content
                .filter((block) => block.type === "text")
                .map((block) => block.text)
                .join("\n"),
            );
        }
      } catch (error) {
        console.error(
          error instanceof AdmissionError
            ? error.message
            : "Operation stopped; no unchecked candidate was saved.",
        );
        if (session.isStopped) {
          console.error("An unfinished tool batch requires a new session.");
          break;
        }
      } finally {
        operation = undefined;
      }
    }
  } finally {
    terminal.close();
  }
}

main().catch((error) => {
  console.error(
    error instanceof AdmissionError
      ? error.message
      : "Example failed; check configuration and service availability.",
  );
  process.exitCode = 1;
});
