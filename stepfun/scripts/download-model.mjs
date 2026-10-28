import { createReadStream, createWriteStream } from "node:fs";
import { mkdir, readFile, rename, stat, writeFile } from "node:fs/promises";
import { pipeline } from "node:stream/promises";
import { Readable } from "node:stream";
import { createHash } from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { MODEL } from "../src/contract.js";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const directory = path.join(root, ".local", "models", MODEL.id);
const common = [
  "config.json",
  "tokenizer.json",
  "tokenizer_config.json",
  "special_tokens_map.json",
  "vocab.txt",
];
const files = [...common, ...Object.values(MODEL.variants).map((v) => v.file)];
async function digest(file) {
  const hash = createHash("sha256");
  for await (const data of createReadStream(file)) hash.update(data);
  return hash.digest("hex");
}
async function download(name) {
  const target = path.join(directory, name);
  const expected = Object.values(MODEL.variants).find((v) => v.file === name);
  let valid = false;
  try {
    valid =
      (await stat(target)).size > 0 &&
      (!expected || (await digest(target)) === expected.sha256);
  } catch {}
  if (!valid) {
    await mkdir(path.dirname(target), { recursive: true });
    console.log("Downloading", name);
    const response = await fetch(
      `https://huggingface.co/${MODEL.id}/resolve/${MODEL.revision}/${name}`,
      { signal: AbortSignal.timeout(180000) },
    );
    if (!response.ok || !response.body)
      throw new Error(
        `Model download failed: ${name}, HTTP ${response.status}`,
      );
    await pipeline(
      Readable.fromWeb(response.body),
      createWriteStream(target + ".partial"),
    );
    if (expected && (await digest(target + ".partial")) !== expected.sha256)
      throw new Error(`SHA-256 mismatch for ${name}`);
    await rename(target + ".partial", target);
  }
  const result = {
    file: name,
    bytes: (await stat(target)).size,
    sha256: await digest(target),
  };
  if (
    expected &&
    (result.bytes !== expected.bytes || result.sha256 !== expected.sha256)
  )
    throw new Error(`Invalid pinned weights: ${name}`);
  return result;
}
const receipts = [];
// Keep concurrent downloads bounded; no model is executed in this process.
for (let i = 0; i < files.length; i += 3)
  receipts.push(...(await Promise.all(files.slice(i, i + 3).map(download))));
const commonBytes = receipts
  .filter((r) => common.includes(r.file))
  .reduce((s, r) => s + r.bytes, 0);
const sizes = Object.fromEntries(
  Object.entries(MODEL.variants).map(([dtype, value]) => [
    dtype,
    {
      weightsBytes: value.bytes,
      packageBytes: value.bytes + commonBytes,
      compressionRatio:
        (MODEL.variants.fp32.bytes + commonBytes) / (value.bytes + commonBytes),
    },
  ]),
);
const report = {
  model: MODEL,
  commonBytes,
  sizes,
  files: receipts,
  inference_executed: false,
};
await writeFile(
  path.join(root, ".local", "model-receipts.json"),
  JSON.stringify(report, null, 2) + "\n",
);
await mkdir(path.join(root, "evidence"), { recursive: true });
await writeFile(
  path.join(root, "evidence", "model-files.json"),
  JSON.stringify(report, null, 2) + "\n",
);
console.log(
  "Pinned model files verified. Model inference will run only in the browser.",
);
console.log(JSON.stringify(sizes, null, 2));
