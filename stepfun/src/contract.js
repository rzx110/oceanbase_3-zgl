export const MODEL = Object.freeze({
  id: "Xenova/all-MiniLM-L6-v2",
  revision: "751bff37182d3f1213fa05d7196b954e230abad9",
  dimension: 384,
  pooling: "mean",
  normalize: true,
  maxTokens: 256,
  variants: {
    fp32: {
      file: "onnx/model.onnx",
      bytes: 90387606,
      sha256:
        "759c3cd2b7fe7e93933ad23c4c9181b7396442a2ed746ec7c1d46192c469c46e",
    },
    fp16: {
      file: "onnx/model_fp16.onnx",
      bytes: 45297825,
      sha256:
        "2cdb5e58291813b6d6e248ed69010100246821a367fa17b1b81ae9483744533d",
    },
    q8: {
      file: "onnx/model_quantized.onnx",
      bytes: 22972370,
      sha256:
        "afdb6f1a0e45b715d0bb9b11772f032c399babd23bfc31fed1c170afc848bdb1",
    },
  },
});
export const SPACE = `${MODEL.id}@${MODEL.revision}:mean:l2:384`;
export function checkVector(vector) {
  if (
    !Array.isArray(vector) ||
    vector.length !== MODEL.dimension ||
    vector.some((x) => typeof x !== "number" || !Number.isFinite(x))
  )
    throw new Error("需要浏览器生成的 384 维有限数值向量");
  const norm = Math.sqrt(vector.reduce((sum, x) => sum + x * x, 0));
  if (Math.abs(norm - 1) > 0.02)
    throw new Error("向量须先在浏览器中做 L2 归一化");
}
export function chooseBackend(hasWebGPU, mode = "auto") {
  if (mode === "wasm-fp32") return { device: "wasm", dtype: "fp32" };
  if (mode === "wasm-q8" || !hasWebGPU) return { device: "wasm", dtype: "q8" };
  if (mode === "webgpu-fp32") return { device: "webgpu", dtype: "fp32" };
  return { device: "webgpu", dtype: "fp16" };
}
export function retrievalMetrics(ids, relevant, k = 3) {
  const gold = new Set(relevant),
    unique = [...new Set(ids)].slice(0, k);
  const hits = unique.filter((id) => gold.has(id)).length;
  const dcg = unique.reduce(
    (sum, id, i) => sum + (gold.has(id) ? 1 / Math.log2(i + 2) : 0),
    0,
  );
  const ideal = Array.from(
    { length: Math.min(k, gold.size) },
    (_, i) => 1 / Math.log2(i + 2),
  ).reduce((a, b) => a + b, 0);
  return {
    recall: gold.size ? hits / gold.size : 0,
    ndcg: ideal ? dcg / ideal : 0,
  };
}
export function median(values) {
  const sorted = [...values].sort((a, b) => a - b);
  if (!sorted.length) return null;
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2
    ? sorted[middle]
    : (sorted[middle - 1] + sorted[middle]) / 2;
}
