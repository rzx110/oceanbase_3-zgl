import "./style.css";
import { MODEL, SPACE, retrievalMetrics, median } from "./contract.js";
import { documents as demoDocs, queries } from "./demo.js";
const $ = (id) => document.getElementById(id);
document.getElementById("app").innerHTML = `
<aside><a class="brand" href="/">端<span>研</span><small>EDGE NOTES</small></a><div class="side-label">你的知识空间</div><div id="collections"></div><button id="new-kb" class="side-button">＋ 新建知识库</button><div class="side-foot"><span class="dot"></span> 浏览器本地推理<br><small>向量与原文持久化于本机 seekdb</small><p>文本不会发送给云端模型。</p></div></aside>
<main><header><span>工作台 / <b id="current-name">知识库</b></span><span class="pill" id="db-state">连接 seekdb…</span></header>
<section class="hero"><div><div class="eyebrow">LOCAL INTELLIGENCE, TRACEABLE KNOWLEDGE</div><h1>让资料，成为线索。</h1><p>在自己的设备上理解文本，找到可回查的原文。</p></div><div class="hero-mark">✳</div></section>
<section class="runtime"><div><span class="side-label">端侧引擎</span><b id="backend">尚未加载</b><small id="model-note">MiniLM-L6 · 384 维 · 英文语义模型</small></div><label class="select-label">推理配置<select id="mode"><option value="auto">自动 · 优先 WebGPU</option><option value="wasm-q8">WASM · INT8</option><option value="wasm-fp32">WASM · FP32 基线</option><option value="webgpu-fp16">WebGPU · FP16</option><option value="webgpu-fp32">WebGPU · FP32 基线</option></select></label><button id="load" class="secondary">加载模型</button></section>
<div class="notice" id="notice" role="status">准备就绪。首次加载从本机读取模型，后续请求复用推理会话。</div>
<section class="workspace"><div class="import card"><div class="section-title"><span class="number">01</span><h2>收集资料</h2><span class="muted" id="doc-count">0 个文本块</span></div><p class="muted">支持 TXT / Markdown。长文按段落切块，保留来源。</p><label>来源名称<input id="source" value="my-notes.md" maxlength="200"></label><label>笔记正文<textarea id="text" rows="6" placeholder="粘贴一段英文资料，或选择本地文本文件…"></textarea></label><div class="actions"><button id="import" class="primary">向量化并导入 ↗</button><label class="file-label">选择文件<input id="files" type="file" accept=".txt,.md,text/plain,text/markdown" multiple></label></div><button id="demo" class="text-button">导入演示集 · 32 篇原创笔记</button><small class="hint">模型针对英文训练。可保存中文原文，中文语义质量未作保证。</small></div>
<div class="search card"><div class="section-title"><span class="number">02</span><h2>发现关联</h2><span class="tag">全文 × 向量</span></div><form id="search-form"><label>用自然语言提问<div class="query-wrap"><input id="query" placeholder="How can I run inference without a GPU?" maxlength="2000" required><button class="primary" type="submit">检索 →</button></div></label></form><div class="examples"><button data-question="How can I run inference without a GPU?">没有 GPU 怎么办？</button><button data-question="How do newly inserted documents become searchable?">新文档何时可检索？</button></div><div id="search-stats" class="muted"></div><div id="results" class="results"><div class="empty"><span>⌕</span><h3>从一个问题开始</h3><p>结果由 seekdb 在数据库内融合排序，<br>每条结果都附有原文与来源。</p></div></div></div></section>
<section class="lab card"><div class="section-title"><span class="number">03</span><h2>可复现实验室</h2><span class="tag">真实推理 · 真实数据库</span></div><p class="muted">同一设备、32 篇笔记、18 个问题。基线与 INT8 / FP16 分别预热，并保留 3 轮原始数据。测试使用独立集合，完成后返回当前知识库。</p><div class="actions"><button id="benchmark" class="secondary">运行量化与性能对比</button><button id="cold" class="secondary">模型冷 / 热加载对照</button><button id="validate" class="secondary">验证增量、幂等与集合隔离</button><button id="fallback" class="secondary">验证禁用 GPU 后自动降级</button></div><div id="bench-table"></div><details><summary>原始实验记录与下载</summary><textarea id="report" aria-label="本轮实测 JSON" readonly rows="10">尚未运行实验。</textarea><a id="download" hidden download="edge-notes-benchmark.json">下载实测 JSON</a></details></section>
<footer>端研 / seekdb × 端侧 AI <span>本机推理 → 本机存储 → 数据库融合 → 原文回查</span></footer></main>`;
let worker = new Worker(new URL("./embedding-worker.js", import.meta.url), {
    type: "module",
  }),
  seq = 0,
  pending = new Map(),
  backend = null,
  busy = false,
  collections = [],
  active = null,
  receipts = null;
let report = {
  createdAt: new Date().toISOString(),
  model: MODEL,
  space: SPACE,
  userAgent: navigator.userAgent,
  hardwareConcurrency: navigator.hardwareConcurrency,
  deviceMemoryGB: navigator.deviceMemory,
  crossOriginIsolated,
  corpus: {
    documents: demoDocs.length,
    queries: queries.length,
    origin: "original deterministic notes in src/demo.js",
  },
  runs: [],
  validations: [],
};
try {
  const previous = JSON.parse(localStorage.getItem("edge-notes-experiment"));
  if (previous?.space === SPACE) report = previous;
} catch {}
worker.onmessage = ({ data }) => {
  if (data.event === "progress") {
    notice(data.text);
    return;
  }
  const p = pending.get(data.id);
  if (p) {
    pending.delete(data.id);
    data.error ? p.reject(new Error(data.error)) : p.resolve(data.result);
  }
};
worker.onerror = (e) => {
  for (const p of pending.values()) p.reject(new Error(e.message));
  pending.clear();
};
function rpc(type, payload = {}) {
  return new Promise((resolve, reject) => {
    const id = ++seq;
    pending.set(id, { resolve, reject });
    worker.postMessage({ id, type, ...payload });
  });
}
function notice(text, error = false) {
  $("notice").textContent = text;
  $("notice").classList.toggle("error", error);
}
function clearResults() {
  $("results").replaceChildren();
  $("search-stats").textContent = "";
}
function lock(value) {
  busy = value;
  document
    .querySelectorAll("button,input,select,textarea:not([readonly])")
    .forEach((el) => {
      el.disabled = value;
    });
}
async function action(work) {
  if (busy) return;
  lock(true);
  try {
    await work();
  } catch (e) {
    notice(e.message, true);
  } finally {
    lock(false);
  }
}
async function api(p, body) {
  const r = await fetch(
    "/api/" + p,
    body === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
  );
  const result = await r.json();
  if (!r.ok) throw new Error(result.error || "数据库请求失败");
  return result;
}
function saveReport() {
  report.updatedAt = new Date().toISOString();
  $("report").value = JSON.stringify(report, null, 2);
  localStorage.setItem("edge-notes-experiment", $("report").value);
  const a = $("download");
  if (a.href.startsWith("blob:")) URL.revokeObjectURL(a.href);
  a.href = URL.createObjectURL(
    new Blob([$("report").value], { type: "application/json" }),
  );
  a.hidden = false;
}
function renderCollections() {
  const host = $("collections");
  host.replaceChildren();
  for (const c of collections) {
    const b = document.createElement("button");
    b.className = "kb" + (c.id === active ? " selected" : "");
    b.textContent = `▤  ${c.label}`;
    b.title = `${c.count} 个文本块`;
    b.disabled = busy;
    b.onclick = () => {
      active = c.id;
      localStorage.setItem("edge-notes-active", active);
      $("results").replaceChildren();
      $("search-stats").textContent = "";
      renderCollections();
    };
    host.append(b);
  }
  const c = collections.find((x) => x.id === active);
  $("current-name").textContent = c?.label || "知识库";
  $("doc-count").textContent = `${c?.count || 0} 个文本块`;
}
async function refresh() {
  collections = await api("collections");
  if (!collections.some((c) => c.id === active)) active = collections[0]?.id;
  if (active) localStorage.setItem("edge-notes-active", active);
  renderCollections();
}
async function load(mode = $("mode").value, disableGpu = false, cold = false) {
  notice("正在加载本地模型…");
  const result = await rpc("load", { mode, disableGpu, cold });
  backend = result;
  $("backend").textContent =
    `${result.device.toUpperCase()} · ${result.dtype === "q8" ? "INT8" : result.dtype.toUpperCase()}`;
  $("model-note").textContent =
    result.reason ||
    `MiniLM-L6 · 384 维 · ${result.reused ? "复用已加载会话" : "模型已加载"}`;
  notice(
    `${result.reused ? "会话复用" : "模型加载完成"} · ${result.loadMs.toFixed(0)} ms${result.reason ? " · " + result.reason : ""}`,
  );
  return result;
}
async function ensureModel() {
  if (!backend) await load();
}
function chunks(text) {
  const out = [];
  let start = 0;
  while (start < text.length) {
    let end = Math.min(start + 800, text.length);
    if (end < text.length) {
      const part = text.slice(start, end),
        match = [...part.matchAll(/\s+/g)].at(-1);
      if (match && match.index > 400)
        end = start + match.index + match[0].length;
    }
    const chunk = text.slice(start, end);
    if (chunk.trim()) out.push(chunk);
    start = end;
  }
  return out;
}
async function sourceRecords(source, text) {
  return Promise.all(
    chunks(text).map(async (text, chunk) => {
      const bytes = new TextEncoder().encode(`${source}\0${chunk}`);
      const hash = await crypto.subtle.digest("SHA-256", bytes);
      return {
        id: [...new Uint8Array(hash)]
          .map((x) => x.toString(16).padStart(2, "0"))
          .join(""),
        source,
        chunk,
        text,
      };
    }),
  );
}
async function ingest(id, docs, batchSize = 8, refreshEach = false) {
  const t = performance.now();
  const inference = await rpc("embed", {
    texts: docs.map((d) => d.text),
    batchSize,
  });
  const vectorized = docs.map((d, i) => ({
    ...d,
    embedding: inference.vectors[i],
    dtype: inference.config.dtype,
    backend: inference.config.device,
  }));
  let writeMs = 0,
    refreshMs = 0,
    result;
  for (const part of refreshEach ? vectorized.map((x) => [x]) : [vectorized]) {
    result = await api(`collections/${id}/import`, {
      space: SPACE,
      documents: part,
    });
    writeMs += result.writeMs;
    refreshMs += result.refreshMs;
  }
  return {
    documentEmbeddingMs: inference.ms,
    batches: inference.batches,
    writeMs,
    refreshMs,
    totalMs: performance.now() - t,
    count: result.count,
    throughput: docs.length / (inference.ms / 1000),
  };
}
function renderResults(result) {
  const host = $("results");
  host.replaceChildren();
  if (!result.results.length) {
    host.textContent = "当前知识库还没有匹配的资料。";
    return;
  }
  result.results.forEach((r, i) => {
    const article = document.createElement("article"),
      head = document.createElement("div"),
      rank = document.createElement("span"),
      name = document.createElement("b"),
      score = document.createElement("small"),
      text = document.createElement("p");
    rank.className = "rank";
    rank.textContent = String(i + 1).padStart(2, "0");
    name.textContent = r.metadata.source;
    score.textContent = `块 ${Number(r.metadata.chunk) + 1} · RRF ${Number(r.score).toFixed(4)}`;
    head.append(rank, name, score);
    text.textContent = r.text;
    article.append(head, text);
    host.append(article);
  });
}
async function search(id, text, limit = 5, render = true) {
  const t = performance.now();
  const query = await rpc("embed", { texts: [text], batchSize: 1 });
  const result = await api(`collections/${id}/search`, {
    text,
    embedding: query.vectors[0],
    limit,
    space: SPACE,
  });
  if (render) {
    renderResults(result);
    $("search-stats").textContent =
      `${result.results.length} 条来源 · 查询向量 ${query.ms.toFixed(0)} ms · seekdb ${result.dbMs.toFixed(1)} ms`;
    $("results").getBoundingClientRect();
  }
  return {
    ...result,
    queryEmbeddingMs: query.ms,
    e2eMs: performance.now() - t,
    visibility: document.visibilityState,
    timingScope:
      "input through worker inference, HTTP, seekdb, result DOM update and layout; excludes display refresh",
  };
}
$("load").onclick = () => action(() => load());
$("mode").onchange = () => action(() => load());
$("new-kb").onclick = () =>
  action(async () => {
    const label = `知识库 ${collections.length + 1}`;
    const c = await api("collections", { label });
    active = c.id;
    clearResults();
    await refresh();
    notice(`已创建 ${label}`);
  });
$("import").onclick = () =>
  action(async () => {
    await ensureModel();
    const docs = await sourceRecords($("source").value.trim(), $("text").value);
    if (!docs.length || !$("source").value.trim())
      throw new Error("请填写来源和正文");
    const id = active;
    const result = await ingest(id, docs);
    await refresh();
    notice(
      `导入完成 · ${result.count} 个文本块 · 索引刷新 ${result.refreshMs.toFixed(1)} ms`,
    );
  });
$("files").onchange = () =>
  action(async () => {
    await ensureModel();
    const docs = [];
    for (const file of $("files").files) {
      if (file.size > 1024 * 1024) throw new Error("单文件上限 1 MB");
      docs.push(...(await sourceRecords(file.name, await file.text())));
    }
    if (!docs.length) throw new Error("未读取到非空文本");
    const result = await ingest(active, docs);
    await refresh();
    notice(`文件导入完成 · ${result.count} 个文本块`);
    $("files").value = "";
  });
$("demo").onclick = () =>
  action(async () => {
    await ensureModel();
    const result = await ingest(active, demoDocs);
    await refresh();
    $("query").value = queries[0].text;
    notice(
      `已导入 ${demoDocs.length} 篇原创笔记 · 当前 ${result.count} 个文本块`,
    );
  });
$("search-form").onsubmit = (e) => {
  e.preventDefault();
  action(async () => {
    await ensureModel();
    const result = await search(active, $("query").value);
    notice(`数据库内混合检索完成 · 端到端 ${result.e2eMs.toFixed(0)} ms`);
  });
};
document.querySelectorAll("[data-question]").forEach((b) => {
  b.onclick = () => {
    $("query").value = b.dataset.question;
    $("search-form").requestSubmit();
  };
});
function benchTable() {
  const div = $("bench-table");
  div.replaceChildren();
  if (!report.runs.length) return;
  const table = document.createElement("table");
  table.innerHTML =
    "<thead><tr><th>配置</th><th>文档 / 秒</th><th>查询 E2E</th><th>DB 检索</th><th>Recall@3</th><th>nDCG@3</th></tr></thead>";
  const body = document.createElement("tbody");
  for (const r of report.runs) {
    if (!r.summary) continue;
    const tr = document.createElement("tr");
    for (const v of [
      r.name,
      r.summary.throughput.toFixed(1),
      r.summary.e2eMs.toFixed(1) + " ms",
      r.summary.dbMs.toFixed(1) + " ms",
      r.summary.recall.toFixed(3),
      r.summary.ndcg.toFixed(3),
    ]) {
      const td = document.createElement("td");
      td.textContent = v;
      tr.append(td);
    }
    body.append(tr);
  }
  table.append(body);
  div.append(table);
}
$("benchmark").onclick = () =>
  action(async () => {
    const original = active;
    report.runs = [];
    report.modelFiles = receipts;
    const profiles = [
      { mode: "wasm-fp32", batch: 1 },
      { mode: "wasm-q8", batch: 1 },
      { mode: "wasm-q8", batch: 8 },
      { mode: "webgpu-fp32", batch: 8 },
      { mode: "webgpu-fp16", batch: 8 },
    ];
    try {
      for (const p of profiles) {
        const config = await load(p.mode);
        if (p.mode.startsWith("webgpu") && config.device !== "webgpu") {
          report.runs.push({
            name: p.mode,
            skipped: true,
            reason: config.reason,
          });
          continue;
        }
        if (p.mode === "webgpu-fp16" && config.dtype !== "fp16") {
          report.runs.push({
            name: p.mode,
            skipped: true,
            reason: config.reason,
          });
          continue;
        }
        const collection = await api("collections", {
          label: `实验 ${p.mode} B${p.batch}`,
        });
        const run = {
          name: `${config.device.toUpperCase()} ${config.dtype.toUpperCase()} B${p.batch}`,
          collection: collection.id,
          config,
          batchSize: p.batch,
          rounds: [],
        };
        report.runs.push(run);
        notice(`${run.name} · 预热与首次请求`);
        run.firstImport = await ingest(collection.id, demoDocs, p.batch);
        run.firstQuery = await search(collection.id, queries[0].text, 3);
        await rpc("embed", { texts: ["warmup sentence"], batchSize: 1 });
        for (let round = 0; round < 3; round++) {
          notice(`${run.name} · 测量 ${round + 1}/3`);
          const entry = {
            round,
            import: await ingest(collection.id, demoDocs, p.batch),
            queries: [],
          };
          for (const q of queries) {
            $("query").value = q.text;
            const result = await search(collection.id, q.text, 3);
            entry.queries.push({
              id: q.id,
              text: q.text,
              relevant: q.relevant,
              returned: result.results.map((r) => r.id),
              queryEmbeddingMs: result.queryEmbeddingMs,
              dbMs: result.dbMs,
              e2eMs: result.e2eMs,
              visibility: result.visibility,
              timingScope: result.timingScope,
              ...retrievalMetrics(
                result.results.map((r) => r.id),
                q.relevant,
                3,
              ),
            });
          }
          run.rounds.push(entry);
          saveReport();
        }
        const qs = run.rounds.flatMap((r) => r.queries);
        run.summary = {
          throughput: median(run.rounds.map((r) => r.import.throughput)),
          documentEmbeddingMs: median(
            run.rounds.map((r) => r.import.documentEmbeddingMs),
          ),
          writeMs: median(run.rounds.map((r) => r.import.writeMs)),
          refreshMs: median(run.rounds.map((r) => r.import.refreshMs)),
          queryEmbeddingMs: median(qs.map((q) => q.queryEmbeddingMs)),
          e2eMs: median(qs.map((q) => q.e2eMs)),
          dbMs: median(qs.map((q) => q.dbMs)),
          recall: qs.reduce((s, q) => s + q.recall, 0) / qs.length,
          ndcg: qs.reduce((s, q) => s + q.ndcg, 0) / qs.length,
        };
        benchTable();
        saveReport();
      }
      // Same model, precision, vectors, batch size and corpus; vary only refresh strategy.
      await load("wasm-q8");
      const c = await api("collections", { label: "索引刷新对照" });
      report.indexStrategy = { config: backend, batchSize: 8, rounds: [] };
      for (let i = 0; i < 3; i++) {
        const each = await ingest(c.id, demoDocs, 8, true),
          batched = await ingest(c.id, demoDocs, 8, false);
        report.indexStrategy.rounds.push({
          eachDocument: each,
          oneBatch: batched,
        });
      }
      notice("量化、批处理与索引刷新对比已完成。原始数据可下载。");
      saveReport();
    } finally {
      active = original;
      clearResults();
      await refresh();
    }
  });
$("validate").onclick = () =>
  action(async () => {
    await ensureModel();
    const original = active;
    const a = await api("collections", { label: "验证 A " + Date.now() }),
      b = await api("collections", { label: "验证 B " + Date.now() });
    const doc = {
      id: "incremental-proof",
      source: "incremental-proof.md",
      chunk: 0,
      text: "The violet telescope calibration certificate is stored in drawer seventeen.",
    };
    const first = await ingest(a.id, [doc]),
      again = await ingest(a.id, [doc]);
    const second = {
      id: "new-proof",
      source: "new-proof.md",
      chunk: 0,
      text: "A newly imported copper lantern maintenance guide recommends replacing the wick every month.",
    };
    const increment = await ingest(a.id, [second]),
      found = await search(a.id, "copper lantern maintenance guide", 5),
      isolated = await search(b.id, "copper lantern maintenance guide", 5),
      back = await search(a.id, "violet telescope calibration certificate", 5);
    const checks = {
      idempotent: first.count === again.count,
      increment: increment.count === first.count + 1,
      newDocumentVisible: found.results.some((r) => r.id === second.id),
      emptyCollectionIsolated: isolated.results.length === 0,
      switchBack: back.results.some((r) => r.id === doc.id),
    };
    report.validations.push({
      type: "real-browser-inference-real-seekdb",
      config: backend,
      collections: [a.id, b.id],
      checks,
      first,
      again,
      increment,
      foundIds: found.results.map((r) => r.id),
      isolatedIds: isolated.results.map((r) => r.id),
      switchBackIds: back.results.map((r) => r.id),
    });
    active = original;
    clearResults();
    await refresh();
    saveReport();
    if (Object.values(checks).some((v) => !v))
      throw new Error("状态验证存在失败，查看原始记录");
    notice("验证通过：增量可见、重复导入幂等、空集合隔离、切换后来源正确。");
  });
$("fallback").onclick = () =>
  action(async () => {
    const result = await load("auto", true);
    const c = await api("collections", { label: "WASM 降级验证" });
    const imported = await ingest(c.id, [demoDocs[0], demoDocs[1]]);
    const found = await search(c.id, queries[0].text, 3);
    const check = {
      type: "GPU-disabled-by-explicit-test-policy",
      notPhysicalHardwareTest: true,
      config: result,
      imported,
      returned: found.results.map((r) => r.id),
      passed: result.device === "wasm" && found.results.length > 0,
    };
    report.validations.push(check);
    clearResults();
    await refresh();
    saveReport();
    if (!check.passed) throw new Error("降级验证失败");
    notice(
      "禁用 GPU 的验证模式通过：自动选择 WASM INT8，完成真实向量化、入库与检索。",
    );
  });
$("cold").onclick = () =>
  action(async () => {
    report.modelLoading = [];
    for (const mode of ["wasm-fp32", "wasm-q8", "webgpu-fp16"]) {
      const cold = await load(mode, false, true);
      const first = await rpc("embed", {
        texts: [queries[0].text],
        batchSize: 1,
      });
      const reused = await load(mode),
        warm = [];
      for (let i = 0; i < 3; i++) {
        const r = await rpc("embed", {
          texts: [queries[0].text],
          batchSize: 1,
        });
        warm.push(r.ms);
      }
      report.modelLoading.push({
        mode,
        cold,
        firstInferenceMs: first.ms,
        reused,
        warmInferenceMs: warm,
        warmMedianMs: median(warm),
        scope:
          "unique same-origin URL, no-store response, browser model cache disabled; ORT runtime may be warm; no Internet inference",
      });
      saveReport();
    }
    notice("模型冷加载、同源文件传输与热会话复用已记录。");
  });
action(async () => {
  const status = await api("status");
  report.database = status;
  $("db-state").textContent = "● " + status.database;
  receipts = await api("model-files");
  active = localStorage.getItem("edge-notes-active");
  await refresh();
  if (!collections.length) {
    const c = await api("collections", { label: "研究笔记" });
    active = c.id;
    await refresh();
  }
  if (report.runs.length || report.validations.length) {
    saveReport();
    benchTable();
  }
  notice("本地 seekdb 已连接。加载模型后即可导入与检索。");
});
