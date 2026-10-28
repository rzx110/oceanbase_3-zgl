import test from "node:test";
import assert from "node:assert/strict";
import {
  checkVector,
  chooseBackend,
  retrievalMetrics,
  median,
} from "../src/contract.js";
test("quantized fallback selection and ranking metrics", () => {
  assert.deepEqual(chooseBackend(false), { device: "wasm", dtype: "q8" });
  assert.deepEqual(chooseBackend(true), { device: "webgpu", dtype: "fp16" });
  assert.deepEqual(chooseBackend(true, "wasm-fp32"), {
    device: "wasm",
    dtype: "fp32",
  });
  assert.deepEqual(retrievalMetrics(["x", "x", "gold"], ["gold"], 3), {
    recall: 1,
    ndcg: 1 / Math.log2(3),
  });
  assert.equal(median([8, 1, 4, 2]), 3);
  assert.throws(() => checkVector([1]));
  assert.throws(() => checkVector(Array(384).fill(0)));
  const vector = Array(384).fill(0);
  vector[0] = 1;
  checkVector(vector);
  vector[1] = NaN;
  assert.throws(() => checkVector(vector));
});
