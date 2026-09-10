// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type { AgentTool } from "@earendil-works/pi-agent-core";
import {
  createReadTool,
  createBashTool,
  createEditTool,
  createWriteTool,
  createGrepTool,
  createFindTool,
  createLsTool,
  createLocalBashOperations,
} from "@earendil-works/pi-coding-agent";

/** Keep bash output below Pi's automatic spill-to-file threshold. */
export function projectTools(cwd: string): AgentTool[] {
  const local = createLocalBashOperations();
  const bash = createBashTool(cwd, {
    exposeSessionEnvironment: false,
    operations: {
      async exec(command, directory, options) {
        const limit = new AbortController();
        let bytes = 0;
        let lines = 0;
        const result = await local.exec(command, directory, {
          ...options,
          signal: AbortSignal.any([
            limit.signal,
            ...(options.signal ? [options.signal] : []),
          ]),
          onData(data) {
            bytes += data.length;
            lines += data.toString("utf8").split("\n").length - 1;
            if (bytes > 16_000 || lines > 1000) limit.abort();
            else if (!limit.signal.aborted) options.onData(data);
          },
        });
        if (limit.signal.aborted)
          throw new Error(
            "Bash output exceeded the example's in-memory limit.",
          );
        return result;
      },
    },
  });
  return [
    createReadTool(cwd),
    bash,
    createEditTool(cwd),
    createWriteTool(cwd),
    createGrepTool(cwd),
    createFindTool(cwd),
    createLsTool(cwd),
  ];
}
