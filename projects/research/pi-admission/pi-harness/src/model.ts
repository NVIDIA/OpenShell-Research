// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { readFile } from "node:fs/promises";
import { join } from "node:path";
import {
  InMemoryCredentialStore,
  InMemoryModelsStore,
  type Model,
} from "@earendil-works/pi-ai";
import { ModelRuntime } from "@earendil-works/pi-coding-agent";

/** Let Pi resolve its native catalog, without writing into the read-only image. */
export async function loadSelectedModel(
  directory = "/app",
): Promise<Model<"openai-completions">> {
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
  return model as Model<"openai-completions">;
}
