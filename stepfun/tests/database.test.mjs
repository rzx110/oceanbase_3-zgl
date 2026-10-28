import test from "node:test";
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { KnowledgeDatabase } from "../src/database.mjs";
import { SPACE } from "../src/contract.js";
test("real local seekdb: atomic validation, replacement, refresh, isolation, reconnect", async () => {
  let db = await KnowledgeDatabase.open();
  const records = [];
  // Synthetic unit vectors verify DB contracts only. They are NOT inference evidence.
  const vector = Array(384).fill(0);
  vector[0] = 1;
  const row = (id, text, source = "test.md") => ({
    id,
    text,
    source,
    chunk: 0,
    dtype: "q8",
    backend: "wasm",
    embedding: vector,
  });
  let a, b;
  try {
    a = await db.create("自动测试 A " + Date.now());
    b = await db.create("自动测试 B " + Date.now());
    const first = await db.upsert(a.id, {
      space: SPACE,
      documents: [
        row("a", "violet telescope calibration"),
        row("trailing", "obsolete appendix"),
      ],
    });
    assert.equal(first.count, 2);
    records.push("two chunks stored");
    const replaced = await db.upsert(a.id, {
      space: SPACE,
      documents: [row("a", "violet telescope calibration revised")],
    });
    assert.equal(replaced.count, 1);
    records.push("obsolete trailing chunk removed");
    const again = await db.upsert(a.id, {
      space: SPACE,
      documents: [row("a", "violet telescope calibration revised")],
    });
    assert.equal(again.count, 1);
    records.push("repeat import idempotent");
    await assert.rejects(
      db.upsert(a.id, {
        space: SPACE,
        documents: [
          row("partial", "must never be written", "partial.md"),
          { ...row("bad", "wrong vector", "bad.md"), embedding: [1] },
        ],
      }),
    );
    assert.equal((await db.list()).find((c) => c.id === a.id).count, 1);
    records.push("invalid entire batch rejected before writes");
    const [found, empty] = await Promise.all([
      db.search(a.id, {
        space: SPACE,
        text: "violet telescope",
        embedding: vector,
        limit: 3,
      }),
      db.search(b.id, {
        space: SPACE,
        text: "violet telescope",
        embedding: vector,
        limit: 3,
      }),
    ]);
    assert.deepEqual(
      found.results.map((r) => r.id),
      ["a"],
    );
    assert.equal(found.results[0].text, "violet telescope calibration revised");
    assert.equal(empty.results.length, 0);
    records.push("concurrent hybrid searches isolated");
    await db.close();
    db = await KnowledgeDatabase.open();
    const reopened = await db.search(a.id, {
      space: SPACE,
      text: "violet telescope",
      embedding: vector,
      limit: 3,
    });
    assert.equal(reopened.results[0].id, "a");
    records.push("client reconnection retains vectors and original text");
    await assert.rejects(
      db.search(a.id, {
        space: SPACE,
        text: "x",
        embedding: vector,
        limit: 21,
      }),
    );
    records.push("return limit validated");
    await mkdir("evidence", { recursive: true });
    await writeFile(
      "evidence/database-contract.json",
      JSON.stringify(
        {
          passed: true,
          synthetic_vectors_only: true,
          browser_inference_test: false,
          serverRestartTest: false,
          database: db.version,
          collections: [a.id, b.id],
          checks: records,
        },
        null,
        2,
      ) + "\n",
    );
  } finally {
    await db.close();
  }
});
