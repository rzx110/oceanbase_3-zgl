import { pipeline, env } from "@huggingface/transformers";
import { MODEL, chooseBackend } from "./contract.js";
env.allowRemoteModels = false;
env.allowLocalModels = true;
env.localModelPath = "/models/";
env.useBrowserCache = true;
env.backends.onnx.wasm.wasmPaths = "/ort/";
env.backends.onnx.wasm.numThreads = Math.max(
  1,
  Math.min(4, self.navigator.hardwareConcurrency || 1),
);
let extractor = null,
  config = null;
const now = () => performance.now();
const nativeFetch = self.fetch.bind(self);
let assetReads = [],
  assetTasks = [];
self.fetch = async (...args) => {
  const started = now(),
    response = await nativeFetch(...args),
    url = String(args[0]?.url || args[0]);
  if (url.includes("/models/") || url.includes("/models-cold/")) {
    const task = response
      .clone()
      .arrayBuffer()
      .then((buffer) => {
        assetReads.push({
          url,
          bytes: buffer.byteLength,
          ms: now() - started,
          status: response.status,
          cacheControl: response.headers.get("cache-control"),
        });
      });
    assetTasks.push(task);
  }
  return response;
};
async function load(mode, disableGpu = false, cold = false) {
  const start = now();
  assetReads = [];
  assetTasks = [];
  let adapter = null,
    adapterError = null;
  if (!disableGpu)
    try {
      adapter = await self.navigator.gpu?.requestAdapter({
        powerPreference: "high-performance",
      });
      if (!adapter && self.navigator.gpu)
        adapter = await self.navigator.gpu.requestAdapter();
    } catch (e) {
      adapterError = e.message;
    }
  let selected = chooseBackend(Boolean(adapter), mode),
    reason = "";
  const info = adapter?.info
    ? {
        vendor: adapter.info.vendor,
        architecture: adapter.info.architecture,
        device: adapter.info.device,
        description: adapter.info.description,
      }
    : null;
  if (
    selected.device === "webgpu" &&
    selected.dtype === "fp16" &&
    !adapter.features.has("shader-f16")
  ) {
    selected = { device: "webgpu", dtype: "fp32" };
    reason = "GPU 不支持 shader-f16，使用 WebGPU FP32；INT8 可在 WASM 运行";
  }
  if (!adapter)
    reason = disableGpu
      ? "验证模式已禁用 GPU，自动降级 WASM INT8"
      : `当前环境无法取得 WebGPU adapter，自动降级 WASM INT8${adapterError ? "：" + adapterError : ""}`;
  if (
    !cold &&
    config?.device === selected.device &&
    config?.dtype === selected.dtype &&
    extractor
  ) {
    config = {
      ...config,
      mode,
      reason,
      gpuAvailable: Boolean(adapter),
      gpuDisabledByTest: disableGpu,
    };
    return { ...config, loadMs: now() - start, reused: true };
  }
  if (extractor) {
    await extractor.dispose();
    extractor = null;
  }
  env.useBrowserCache = !cold;
  env.localModelPath = cold ? `/models-cold/${Date.now()}/` : "/models/";
  const progress_callback = (p) => {
    if (p.status === "progress")
      self.postMessage({
        event: "progress",
        text: `模型加载 ${p.file}: ${Math.round(p.progress || 0)}%`,
      });
  };
  let failedGpu = null;
  try {
    extractor = await pipeline("feature-extraction", MODEL.id, {
      device: selected.device,
      dtype: selected.dtype,
      progress_callback,
    });
  } catch (e) {
    if (selected.device !== "webgpu") throw e;
    failedGpu = e.message;
    selected = { device: "wasm", dtype: "q8" };
    reason = "WebGPU 初始化失败，自动降级 WASM INT8：" + e.message;
    extractor = await pipeline("feature-extraction", MODEL.id, {
      device: "wasm",
      dtype: "q8",
      progress_callback,
    });
  }
  config = {
    ...selected,
    mode,
    reason,
    gpuAvailable: Boolean(adapter),
    gpuApiAvailable: Boolean(self.navigator.gpu),
    adapterError,
    gpuDisabledByTest: disableGpu,
    gpuInfo: info,
    failedGpu,
    threads: env.backends.onnx.wasm.numThreads,
    model: MODEL.id,
    revision: MODEL.revision,
  };
  await Promise.all(assetTasks);
  return {
    ...config,
    loadMs: now() - start,
    reused: false,
    coldModelTransport: cold,
    assetReads,
    resources: performance
      .getEntriesByType("resource")
      .filter(
        (e) =>
          e.startTime >= start &&
          (e.name.includes("/models/") ||
            e.name.includes("/models-cold/") ||
            e.name.includes("/ort/")),
      )
      .map((e) => ({
        name: e.name.split("/").slice(-2).join("/"),
        duration: e.duration,
        transferSize: e.transferSize,
        encodedBodySize: e.encodedBodySize,
      })),
  };
}
async function embed(texts, batchSize) {
  if (!extractor) throw new Error("请先加载端侧模型");
  if (
    !Array.isArray(texts) ||
    texts.some((t) => typeof t !== "string" || !t.trim())
  )
    throw new Error("文本不能为空");
  const vectors = [],
    batches = [],
    start = now();
  for (let i = 0; i < texts.length; i += batchSize) {
    const t = now();
    const output = await extractor(texts.slice(i, i + batchSize), {
      pooling: "mean",
      normalize: true,
      truncation: true,
      max_length: MODEL.maxTokens,
      padding: true,
    });
    vectors.push(...output.tolist());
    batches.push(now() - t);
    output.dispose?.();
    self.postMessage({
      event: "progress",
      text: `端侧向量化 ${Math.min(texts.length, i + batchSize)} / ${texts.length} · ${config.device.toUpperCase()} ${config.dtype.toUpperCase()}`,
    });
  }
  return { vectors, ms: now() - start, batches, config };
}
self.onmessage = async ({ data }) => {
  try {
    let result;
    if (data.type === "load")
      result = await load(
        data.mode || "auto",
        data.disableGpu || false,
        data.cold || false,
      );
    else if (data.type === "embed")
      result = await embed(
        data.texts,
        Math.max(1, Math.min(32, data.batchSize || 8)),
      );
    else throw new Error("未知推理请求");
    self.postMessage({ id: data.id, result });
  } catch (e) {
    self.postMessage({ id: data.id, error: e.message });
  }
};
