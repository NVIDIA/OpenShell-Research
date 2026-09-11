import assert from "node:assert/strict";
import test from "node:test";
import { toPiJobResponse } from "./responses.ts";

test("keeps cleanup diagnostics out of Pi's strict external-job protocol", () => {
  const response = {
    providerJobId: "job-1",
    state: "completed",
    output: "DONE",
    cleanupError: "gateway unavailable",
    sandboxName: "pi-child-1",
  };
  assert.deepEqual(toPiJobResponse(response), {
    providerJobId: "job-1", state: "completed", output: "DONE",
  });
  assert.equal(response.cleanupError, "gateway unavailable");
});

test("preserves failure diagnostics and normal job handles", () => {
  const response = {
    providerJobId: "job-1", state: "failed",
    failureCode: "child-exit", failureMessage: "Pi failed",
  };
  assert.deepEqual(toPiJobResponse(response), response);
});
