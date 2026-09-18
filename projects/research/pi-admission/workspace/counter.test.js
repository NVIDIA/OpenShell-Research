import assert from "node:assert/strict";
import test from "node:test";
import { increment } from "./counter.js";

test("increments a number", () => assert.equal(increment(1), 2));
