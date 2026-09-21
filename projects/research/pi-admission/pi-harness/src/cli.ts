// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { InteractiveMode } from "@earendil-works/pi-coding-agent";
import { AdmissionError } from "./admission.js";
import { createAdmissionRuntime } from "./session.js";
import { loadSessionOptions } from "./config.js";

async function main(): Promise<void> {
  const { mode, session } = await loadSessionOptions();
  const runtime = await createAdmissionRuntime(session);
  console.log(`Local admission: ${mode}`);
  console.log(`Transcript: ${runtime.session.sessionFile}`);
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
