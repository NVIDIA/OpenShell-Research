// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";

import { defineTool, type ExtensionAPI } from "@earendil-works/pi-coding-agent";
import Ajv2020 from "ajv/dist/2020.js";
import { Type } from "typebox";

const runtimeRoot = process.env.OAR_RUNTIME_ROOT || "/sandbox/oar-runtime";
const schema = JSON.parse(
  readFileSync(`${runtimeRoot}/output.schema.json`, "utf8"),
);
const validate = new Ajv2020({ allErrors: true }).compile(schema);
const parameters = Type.Object({ result: Type.Unsafe(schema) });
const outputDirectory = "/sandbox/artifacts";
const outputPath = `${outputDirectory}/result`;

const submitResult = defineTool({
  name: "submit_result",
  label: "Submit Result",
  description: "Validate and save the final task result.",
  promptSnippet: "Submit the final result using the configured output schema",
  promptGuidelines: [
    "Call submit_result only when the task is complete.",
    "Correct every validation error and call submit_result again if it is rejected.",
    "Do not return the result as assistant text.",
  ],
  parameters,
  async execute(_toolCallId, { result }) {
    if (!validate(result)) {
      const diagnostics = (validate.errors || [])
        .slice(0, 12)
        .map((error) => `${error.instancePath || "/"}: ${error.message || "invalid"}`)
        .join("\n");
      return {
        content: [{ type: "text" as const, text: `Result rejected:\n${diagnostics}` }],
        details: { accepted: false, diagnostics },
        isError: true,
      };
    }

    mkdirSync(outputDirectory, { recursive: true, mode: 0o700 });
    const temporaryPath = `${outputPath}.tmp`;
    writeFileSync(temporaryPath, `${JSON.stringify(result, null, 2)}\n`, {
      encoding: "utf8",
      mode: 0o600,
    });
    renameSync(temporaryPath, outputPath);
    return {
      content: [{ type: "text" as const, text: "Result accepted." }],
      details: { accepted: true, outputPath },
      terminate: true,
    };
  },
});

export default function (pi: ExtensionAPI) {
  pi.registerTool(submitResult);
}
