// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

await import("/sandbox/oar-submit-result.ts");
const validator = await import("/sandbox/oar-validate-tools.ts");

const missing = validator.findMissingTools(["missing"], [{ name: "read" }]);
if (missing[0] !== "missing") {
  throw new Error("the tool validator did not report a missing tool");
}

let beforeAgentStart;
validator.default({
  on(event, handler) {
    if (event === "before_agent_start") {
      beforeAgentStart = handler;
    }
  },
});
if (!beforeAgentStart) {
  throw new Error("the tool validator did not register before_agent_start");
}
await beforeAgentStart(
  {},
  {
    getAllTools: () => [{ name: "read" }],
    getActiveTools: () => ["read"],
  },
);
