import assert from "node:assert/strict";
import test from "node:test";

import { JobStatusBatcher } from "./job-status.ts";

test("coalesces concurrent status reads into one batch", async () => {
  const batches: string[][] = [];
  const batcher = new JobStatusBatcher(async (jobIds) => {
    batches.push(jobIds);
    return jobIds.map((providerJobId) => ({ providerJobId, state: "running" as const }));
  }, 1);

  const results = await Promise.all([
    batcher.get("job-a"),
    batcher.get("job-b"),
    batcher.get("job-a"),
  ]);

  assert.deepEqual(batches, [["job-a", "job-b"]]);
  assert.deepEqual(results.map((result) => result.providerJobId), ["job-a", "job-b", "job-a"]);
});

test("rejects every waiter when the batch load fails", async () => {
  const batcher = new JobStatusBatcher(async () => {
    throw new Error("service unavailable");
  }, 1);

  const results = await Promise.allSettled([batcher.get("job-a"), batcher.get("job-b")]);

  assert.deepEqual(results.map((result) => result.status), ["rejected", "rejected"]);
});
