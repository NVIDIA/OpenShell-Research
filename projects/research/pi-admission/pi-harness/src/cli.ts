// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { randomUUID } from "node:crypto";
import { parseArgs } from "node:util";
import { InteractiveMode } from "@earendil-works/pi-coding-agent";
import { Admission, AdmissionError, createHttpEvaluator } from "./admission.js";
import { createAdmissionRuntime } from "./session.js";
import { configureProxy } from "./network.js";
import { loadSelectedModel } from "./model.js";

async function main(): Promise<void> {
  configureProxy();
  const { values } = parseArgs({
    options: {
      admission: { type: "string" },
    },
  });
  const apiKey = process.env.PI_MODEL_API_KEY;
  const admissionKey = process.env.PI_ADMISSION_TOKEN;
  delete process.env.PI_MODEL_API_KEY;
  delete process.env.PI_ADMISSION_TOKEN;
  if (!apiKey || !admissionKey || !values.admission)
    throw new Error("Missing provider or admission configuration.");
  const model = await loadSelectedModel();
  const runtime = await createAdmissionRuntime({
    cwd: "/sandbox/workspace",
    sessionDir: "/sandbox/sessions",
    agentDir: "/app/agent",
    model,
    apiKey,
    admission: new Admission(
      createHttpEvaluator(values.admission, admissionKey, randomUUID()),
    ),
  });
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
