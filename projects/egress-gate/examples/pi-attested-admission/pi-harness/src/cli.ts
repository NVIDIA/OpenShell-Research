// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { randomUUID } from "node:crypto";
import { parseArgs } from "node:util";
import { InteractiveMode, convertToLlm } from "@earendil-works/pi-coding-agent";
import { Admission, AdmissionError, createHttpEvaluator } from "./admission.js";
import { createAdmissionRuntime } from "./session.js";
import { configureProxy } from "./network.js";
import { loadSelectedModel } from "./model.js";

async function main(): Promise<void> {
  configureProxy();
  const { values } = parseArgs({
    options: {
      cwd: { type: "string", default: "/sandbox/project" },
      "session-dir": { type: "string", default: "/sandbox/sessions" },
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
  const model = await loadSelectedModel();
  const runtime = await createAdmissionRuntime({
    cwd: values.cwd,
    sessionDir: values["session-dir"],
    agentDir: "/app/agent",
    model,
    apiKey,
    admission: new Admission(
      createHttpEvaluator(values.admission, admissionKey, randomUUID()),
    ),
  });
  if (values.prompt !== undefined) {
    try {
      await runtime.session.bindExtensions({});
      await runtime.session.prompt(values.prompt);
      console.log(
        JSON.stringify(convertToLlm(runtime.session.messages), null, 2),
      );
    } finally {
      await runtime.dispose();
    }
    return;
  }
  await new InteractiveMode(runtime).run();
}

main().catch((error) => {
  console.error(
    error instanceof AdmissionError
      ? error.message
      : "Example failed; check configuration and service availability.",
  );
  process.exitCode = 1;
});
