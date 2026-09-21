// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { parseArgs } from "node:util";
import {
  InMemoryCredentialStore,
  InMemoryModelsStore,
  type Model,
} from "@earendil-works/pi-ai";
import { ModelRuntime } from "@earendil-works/pi-coding-agent";
import { EnvHttpProxyAgent, setGlobalDispatcher } from "undici";
import { Admission, type AdmissionMode } from "./admission.js";
import type { SessionOptions } from "./session.js";

export interface LoadedSessionOptions {
  mode: AdmissionMode;
  session: SessionOptions;
}

/** Shared launcher/verification setup; the credential stays out of tool environments. */
export async function loadSessionOptions(
  directory = "/app",
): Promise<LoadedSessionOptions> {
  setGlobalDispatcher(new EnvHttpProxyAgent({ proxyTunnel: true, allowH2: false }));
  const { values } = parseArgs({
    options: { admission: { type: "string" } },
  });
  const mode = values.admission;
  if (mode !== "off" && mode !== "on")
    throw new Error("Pass exactly one of --admission off or --admission on.");
  const apiKey = process.env.PI_MODEL_API_KEY;
  delete process.env.PI_MODEL_API_KEY;
  if (!apiKey) throw new Error("Missing PI_MODEL_API_KEY.");
  const { provider, id } = JSON.parse(
    await readFile(join(directory, "model-selection.json"), "utf8"),
  ) as { provider: string; id: string };
  const runtime = await ModelRuntime.create({
    credentials: new InMemoryCredentialStore(),
    modelsStore: new InMemoryModelsStore(),
    modelsPath: join(directory, "models.json"),
  });
  const error = runtime.getError();
  if (error) throw new Error(error);
  const model = runtime.getModel(provider, id);
  if (!model || model.api !== "openai-completions")
    throw new Error("The prepared model must use openai-completions.");
  return {
    mode,
    session: {
      cwd: "/sandbox/workspace",
      sessionDir: "/sandbox/sessions",
      agentDir: "/app/agent",
      model: model as Model<"openai-completions">,
      apiKey,
      admission: new Admission(mode),
    },
  };
}
