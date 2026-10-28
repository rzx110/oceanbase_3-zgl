import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { isDeepStrictEqual } from "node:util";
import { SeekdbClient } from "seekdb";
import { config } from "./database.mjs";
const file = "evidence/persistence-restart.json";
const phase = process.argv[2];
if (!["before", "after"].includes(phase))
  throw new Error(
    "Use before or after; restart actual database between phases.",
  );
const client = new SeekdbClient(await config());
try {
  let record;
  if (phase === "before") {
    const rows = await client.execute(
      "SELECT id, label FROM stepfun_catalog ORDER BY created_at",
    );
    const kb = rows.find((x) => x.label === "研究笔记");
    if (!kb) throw new Error("Import the real browser demo first");
    record = {
      collection: kb.id,
      startedAt: new Date().toISOString(),
      test: "actual server process restart; stored browser embeddings compared by digest, then browser query re-executed",
    };
  } else record = JSON.parse(await readFile(file, "utf8"));
  const c = await client.getCollection({
    name: record.collection,
    embeddingFunction: null,
  });
  const rows = await c.get({
    limit: 10000,
    include: ["documents", "metadatas", "embeddings"],
  });
  const sha = (value) =>
    createHash("sha256").update(JSON.stringify(value)).digest("hex");
  const snapshot = rows.ids
    .map((id, i) => ({
      id,
      source: rows.metadatas[i].source,
      chunk: rows.metadatas[i].chunk,
      textSha256: sha(rows.documents[i]),
      vectorSha256: sha(rows.embeddings[i]),
    }))
    .sort((a, b) => a.id.localeCompare(b.id));
  if (phase === "before") {
    record.before = snapshot;
    record.beforeCount = await c.count();
  } else {
    record.afterCount = await c.count();
    record.completedAt = new Date().toISOString();
    record.passed =
      isDeepStrictEqual(record.before, snapshot) &&
      record.beforeCount === record.afterCount;
    if (!record.passed) throw new Error("Persistence mismatch");
  }
  await writeFile(file, JSON.stringify(record, null, 2) + "\n");
  console.log(
    JSON.stringify({
      phase,
      collection: record.collection,
      count: snapshot.length,
      passed: record.passed,
    }),
  );
} finally {
  await client.close();
}
