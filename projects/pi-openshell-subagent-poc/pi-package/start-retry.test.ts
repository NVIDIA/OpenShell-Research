import assert from "node:assert/strict";
import test from "node:test";

import { retryAmbiguousStart } from "./start-retry.ts";

test("retries an ambiguous start with bounded backoff", async () => {
  const requestBody = JSON.stringify({ idempotencyKey: "stable-key", prompt: "task" });
  const bodies: string[] = [];
  const delays: number[] = [];
  let attempts = 0;

  const result = await retryAmbiguousStart(
    async () => {
      attempts += 1;
      bodies.push(requestBody);
      if (attempts < 3) throw new Error("ambiguous-timeout");
      return { providerJobId: "job-a", state: "queued" };
    },
    (error) => error instanceof Error && error.message === "ambiguous-timeout",
    () => undefined,
    async (milliseconds) => {
      delays.push(milliseconds);
    },
  );

  assert.deepEqual(result, { providerJobId: "job-a", state: "queued" });
  assert.deepEqual(bodies, [requestBody, requestBody, requestBody]);
  assert.deepEqual(delays, [250, 500]);
});

test("does not retry a definitive rejection", async () => {
  let attempts = 0;
  await assert.rejects(
    retryAmbiguousStart(
      async () => {
        attempts += 1;
        throw new Error("HTTP 429");
      },
      () => false,
      () => undefined,
      async () => undefined,
    ),
    /HTTP 429/,
  );
  assert.equal(attempts, 1);
});
