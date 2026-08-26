// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { readFileSync } from "node:fs";

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const runtimeRoot = process.env.OAR_RUNTIME_ROOT || "/sandbox/oar-runtime";
const requestedTools = JSON.parse(
  readFileSync(`${runtimeRoot}/tools.json`, "utf8"),
) as string[];

export function findMissingTools(
  requested: string[],
  available: Array<{ name: string }>,
): string[] {
  const availableNames = new Set(available.map((tool) => tool.name));
  return requested.filter((name) => !availableNames.has(name));
}

export default function (pi: ExtensionAPI) {
  pi.on("before_agent_start", (_event, context) => {
    const availableTools = context.getAllTools();
    const missingTools = findMissingTools(requestedTools, availableTools);
    const activeTools = context.getActiveTools();
    const activeNames = new Set(activeTools);
    const inactiveTools = requestedTools.filter((name) => !activeNames.has(name));
    const unavailableTools = [...new Set([...missingTools, ...inactiveTools])];
    if (unavailableTools.length === 0) return;

    const availableNames = availableTools
      .map((tool) => tool.name)
      .sort()
      .join(", ");
    process.stderr.write(
      `OAR tool validation failed: unavailable tools: ${unavailableTools.join(", ")}. ` +
        `Registered tools: ${availableNames || "none"}. ` +
        `Active tools: ${activeTools.sort().join(", ") || "none"}.\n`,
    );
    process.exit(2);
  });
}
