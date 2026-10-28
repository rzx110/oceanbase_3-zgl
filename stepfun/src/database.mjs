import { randomUUID } from "node:crypto";
import { performance } from "node:perf_hooks";
import {
  SeekdbClient,
  Schema,
  FulltextIndexConfig,
  VectorIndexConfig,
} from "seekdb";
import { config } from "../scripts/database.mjs";
import { MODEL, SPACE, checkVector } from "./contract.js";
export class KnowledgeDatabase {
  constructor(client) {
    this.client = client;
    this.tail = Promise.resolve();
    this.handles = new Map();
  }
  // hybridSearch uses a connection-scoped SQL variable; serialize the ENTIRE SDK operation.
  serial(work) {
    const next = this.tail.then(work);
    this.tail = next.catch(() => {});
    return next;
  }
  static async open() {
    const db = new KnowledgeDatabase(new SeekdbClient(await config()));
    db.version = (
      await db.client.execute("SELECT VERSION() AS version")
    )[0].version;
    await db.client.execute(
      "CREATE TABLE IF NOT EXISTS stepfun_catalog (id VARCHAR(64) PRIMARY KEY, label VARCHAR(120) NOT NULL, model_space VARCHAR(240) NOT NULL, created_at VARCHAR(40) NOT NULL)",
    );
    return db;
  }
  async row(id) {
    if (!/^edge_[a-f0-9]{32}$/.test(id)) throw new Error("非法知识库 ID");
    const rows = await this.client.execute(
      "SELECT * FROM stepfun_catalog WHERE id=?",
      [id],
    );
    if (!rows?.length || rows[0].model_space !== SPACE)
      throw new Error("知识库不存在或模型空间不兼容");
    return rows[0];
  }
  async collection(id) {
    await this.row(id);
    if (!this.handles.has(id))
      this.handles.set(
        id,
        await this.client.getCollection({ name: id, embeddingFunction: null }),
      );
    return this.handles.get(id);
  }
  list() {
    return this.serial(async () => {
      const rows = await this.client.execute(
        "SELECT * FROM stepfun_catalog ORDER BY created_at",
      );
      for (const row of rows)
        row.count = await (await this.collection(row.id)).count();
      return rows;
    });
  }
  create(label) {
    return this.serial(async () => {
      if (typeof label !== "string" || !label.trim() || label.length > 120)
        throw new Error("名称须为 1–120 个字符");
      const id = "edge_" + randomUUID().replaceAll("-", "");
      const c = await this.client.createCollection({
        name: id,
        schema: new Schema({
          fulltextIndex: new FulltextIndexConfig("space"),
          vectorIndex: new VectorIndexConfig({
            hnsw: { dimension: MODEL.dimension, distance: "cosine" },
            embeddingFunction: null,
          }),
        }),
      });
      await this.client.execute(
        "INSERT INTO stepfun_catalog VALUES (?,?,?,?)",
        [id, label.trim(), SPACE, new Date().toISOString()],
      );
      this.handles.set(id, c);
      return { id, label: label.trim(), count: 0 };
    });
  }
  upsert(id, payload) {
    return this.serial(async () => {
      if (payload.space !== SPACE)
        throw new Error("模型空间不兼容，请切换到固定版本模型");
      const docs = payload.documents;
      if (!Array.isArray(docs) || !docs.length || docs.length > 500)
        throw new Error("每次导入 1–500 个文本块");
      const seen = new Set();
      for (const d of docs) {
        if (
          typeof d.id !== "string" ||
          !d.id ||
          d.id.length > 180 ||
          seen.has(d.id)
        )
          throw new Error("文本块 ID 无效或重复");
        seen.add(d.id);
        if (
          typeof d.text !== "string" ||
          !d.text.trim() ||
          d.text.length > 6000 ||
          typeof d.source !== "string" ||
          !d.source ||
          d.source.length > 240
        )
          throw new Error("原文或来源无效");
        if (
          !["q8", "fp16", "fp32"].includes(d.dtype) ||
          !["wasm", "webgpu"].includes(d.backend)
        )
          throw new Error("缺少真实推理配置");
        checkVector(d.embedding);
      }
      const c = await this.collection(id),
        start = performance.now();
      const metadata = docs.map((d) => ({
        source: d.source,
        chunk: d.chunk ?? 0,
        dtype: d.dtype,
        backend: d.backend,
        model: MODEL.id,
        revision: MODEL.revision,
      }));
      // Replacement removes obsolete trailing chunks from the same imported source.
      const old = [];
      for (const source of new Set(docs.map((d) => d.source))) {
        const existing = await c.get({
          where: { source },
          limit: 10000,
          include: ["metadatas"],
        });
        old.push(...existing.ids.filter((x) => !seen.has(x)));
      }
      await c.upsert({
        ids: docs.map((d) => d.id),
        documents: docs.map((d) => d.text),
        embeddings: docs.map((d) => d.embedding),
        metadatas: metadata,
      });
      if (old.length) await c.delete({ ids: old });
      const writeMs = performance.now() - start,
        refreshStart = performance.now();
      await c.refresh_index();
      return {
        inserted: docs.length,
        count: await c.count(),
        writeMs,
        refreshMs: performance.now() - refreshStart,
        refresh: "seekdb dbms_index_manager.refresh",
        collection: id,
      };
    });
  }
  search(id, { text, embedding, limit = 5, space }) {
    return this.serial(async () => {
      if (
        space !== SPACE ||
        typeof text !== "string" ||
        !text.trim() ||
        text.length > 2000
      )
        throw new Error("查询或模型空间无效");
      checkVector(embedding);
      if (!Number.isInteger(limit) || limit < 1 || limit > 20)
        throw new Error("limit 范围 1–20");
      const c = await this.collection(id),
        start = performance.now();
      const params = {
        query: {
          whereDocument: { $contains: text },
          nResults: Math.max(20, limit),
        },
        knn: { queryEmbeddings: [embedding], nResults: Math.max(20, limit) },
        rank: {
          rrf: { rankConstant: 60, rankWindowSize: Math.max(20, limit) },
        },
        nResults: limit,
        include: ["documents", "metadatas", "distances"],
      };
      const result = await c.hybridSearch(params);
      return {
        collection: id,
        dbMs: performance.now() - start,
        fusion: "seekdb DBMS_HYBRID_SEARCH + RRF (database execution)",
        results: (result.ids?.[0] || []).map((key, i) => ({
          id: key,
          text: result.documents[0][i],
          metadata: result.metadatas[0][i],
          score: result.distances?.[0]?.[i],
        })),
      };
    });
  }
  async close() {
    await this.tail;
    await this.client.close();
  }
}
