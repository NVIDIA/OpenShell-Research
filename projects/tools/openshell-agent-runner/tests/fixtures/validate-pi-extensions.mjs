// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

// Run inside the pinned Pi image with --network none. These checks use Pi's
// actual extension loader, tool registry, and event dispatch without inference.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { copyFileSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  createAgentSession,
  DefaultResourceLoader,
  SessionManager,
  SettingsManager,
} from "@earendil-works/pi-coding-agent";

const scenario = process.argv[2];
if (!scenario) {
  for (const name of ["valid", "missing", "inactive", "no-tools"]) {
    const runtime = mkdtempSync(join(tmpdir(), "oar-pi-contract-"));
    try {
      copyFileSync("/sandbox/output.schema.json", join(runtime, "output.schema.json"));
      const result = spawnSync(process.execPath, [import.meta.filename, name], {
        encoding: "utf8",
        env: { ...process.env, OAR_RUNTIME_ROOT: runtime, PI_OFFLINE: "1" },
        timeout: 30_000,
      });
      assert.ifError(result.error);
      assert.equal(result.status, ["valid", "no-tools"].includes(name) ? 0 : 2,
        `${name}: ${result.stdout}\n${result.stderr}`);
      if (["missing", "inactive"].includes(name)) {
        assert.match(result.stderr, /OAR tool validation failed: unavailable tools:/);
        assert.match(result.stderr, name === "missing" ? /missing_tool/ : /submit_result/);
      }
      console.log(`Pi runtime contract: ${name} passed`);
    } finally {
      rmSync(runtime, { recursive: true, force: true });
    }
  }
} else {
  const runtime = process.env.OAR_RUNTIME_ROOT;
  const requested = scenario === "no-tools" ? [] : ["read", "submit_result"];
  if (scenario === "missing") requested.push("missing_tool");
  writeFileSync(join(runtime, "tools.json"), JSON.stringify(requested));
  const settingsManager = SettingsManager.inMemory();
  const resourceLoader = new DefaultResourceLoader({
    cwd: runtime,
    agentDir: runtime,
    settingsManager,
    noExtensions: true,
    noSkills: true,
    noPromptTemplates: true,
    noThemes: true,
    noContextFiles: true,
    additionalExtensionPaths: [
      "/sandbox/oar-submit-result.ts",
      "/sandbox/oar-validate-tools.ts",
    ],
  });
  await resourceLoader.reload();
  assert.deepEqual(resourceLoader.getExtensions().errors, []);
  const { session } = await createAgentSession({
    cwd: runtime,
    agentDir: runtime,
    resourceLoader,
    settingsManager,
    sessionManager: SessionManager.inMemory(),
    tools: scenario === "inactive" ? ["read"] : requested,
  });
  try {
    await session.bindExtensions({});
    const errors = [];
    session.extensionRunner.onError((error) => errors.push(error));
    await session.extensionRunner.emitBeforeAgentStart("Check tools", undefined, "", {});
    assert.deepEqual(errors, [], "Pi must not swallow a validator runtime error");
    if (scenario === "valid") {
      const tool = session.extensionRunner.getToolDefinition("submit_result");
      assert.ok(tool, "the real submission extension must register its tool");
      const rejected = await tool.execute("invalid", { result: {} });
      assert.equal(rejected.isError, true);
      const result = "not a date-time; format is an annotation";
      const accepted = await tool.execute("valid", { result });
      assert.equal(accepted.details.accepted, true);
      assert.equal(accepted.terminate, true);
      const rejectedAgain = await tool.execute("invalid-again", { result: {} });
      assert.equal(rejectedAgain.isError, true);
      assert.deepEqual(JSON.parse(readFileSync("/sandbox/artifacts/result", "utf8")), result);
    }
  } finally {
    session.dispose();
  }
}
