import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
process.env.SEEKDB_BINDINGS_CACHE_DIR ??= path.join(root, ".local", "bindings");
const { SeekdbClient, Schema, FulltextIndexConfig, VectorIndexConfig } =
  await import("seekdb");
const { config } = await import("./database.mjs");
const client = new SeekdbClient(await config());
try {
  const version = await client.execute("SELECT VERSION() AS version");
  const name = "stepfun_smoke_" + Date.now();
  const collection = await client.createCollection({
    name,
    schema: new Schema({
      fulltextIndex: new FulltextIndexConfig("space"),
      vectorIndex: new VectorIndexConfig({
        hnsw: { dimension: 3, distance: "cosine" },
        embeddingFunction: null,
      }),
    }),
  });
  await collection.upsert({
    ids: ["coffee", "tea", "db"],
    documents: [
      "coffee cafe espresso",
      "tea leaves kettle",
      "database vector index",
    ],
    embeddings: [
      [1, 0, 0],
      [0.9, 0.1, 0],
      [0, 0, 1],
    ],
    metadatas: [{ group: "drink" }, { group: "drink" }, { group: "software" }],
  });
  await collection.refresh_index();
  const result = await collection.hybridSearch({
    query: { whereDocument: { $contains: "coffee" }, nResults: 3 },
    knn: { queryEmbeddings: [[1, 0, 0]], nResults: 3 },
    rank: { rrf: { rankConstant: 60, rankWindowSize: 3 } },
    nResults: 3,
    include: ["documents", "metadatas", "distances"],
  });
  if (!result.ids?.flat().includes("coffee"))
    throw new Error("Real hybrid search did not return the inserted source");
  const report = {
    kind: "real-local-seekdb-js-smoke",
    synthetic_vectors_only: true,
    browser_inference_test: false,
    version,
    collection: name,
    count: await collection.count(),
    result,
  };
  await mkdir(path.join(root, "evidence"), { recursive: true });
  await writeFile(
    path.join(root, "evidence", "database-smoke.json"),
    JSON.stringify(report, null, 2) + "\n",
  );
  console.log(JSON.stringify(report, null, 2));
} finally {
  await client.close();
}
