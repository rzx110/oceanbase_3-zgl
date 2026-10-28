import { createHash } from "node:crypto";
import {
  mkdir,
  readFile,
  writeFile,
  readdir,
  copyFile,
} from "node:fs/promises";
import { execFileSync } from "node:child_process";
import path from "node:path";
import { root } from "./database.mjs";
if (process.platform !== "win32") {
  console.log(
    "Linux/macOS: npm start uses seekdb-js embedded mode automatically.",
  );
  process.exit(0);
}
const url =
  "https://proxy.golang.org/github.com/oceanbase/seekdb-go/native/windows-amd64/@v/v1.4.0.zip";
const sha = "49b52927b5c379cec13161a9528a29a0cff5cd61d74b1c45692c5024d9857395";
const dir = path.join(root, ".local", "windows-runtime");
await mkdir(dir, { recursive: true });
const zip = path.join(dir, "native.zip");
let bytes;
try {
  bytes = await readFile(zip);
} catch {}
if (!bytes || createHash("sha256").update(bytes).digest("hex") !== sha) {
  console.log(
    "Downloading official seekdb-go native Windows runtime v1.4.0...",
  );
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Download ${response.status}`);
  bytes = Buffer.from(await response.arrayBuffer());
  if (createHash("sha256").update(bytes).digest("hex") !== sha)
    throw new Error("Runtime checksum mismatch");
  await writeFile(zip, bytes);
}
execFileSync("tar", ["-xf", zip, "-C", dir], { windowsHide: true });
const archive = path.join(
  dir,
  "github.com/oceanbase/seekdb-go/native/windows-amd64@v1.4.0/bundle.tar.gz",
);
const bundle = await readFile(archive);
if (
  createHash("sha256").update(bundle).digest("hex") !==
  "3a50225198ac67f26f1777dd02cd6a5357ceb0a7ed33fa78db05a2e7a33af295"
)
  throw new Error("Native bundle checksum mismatch");
const staging = path.join(dir, "verified-extraction");
await mkdir(staging, { recursive: true });
execFileSync("tar", ["-xzf", archive, "-C", staging], { windowsHide: true });
for (const name of await readdir(staging)) {
  const src = path.join(staging, name),
    dest = path.join(dir, name);
  let same = false;
  try {
    same =
      createHash("sha256")
        .update(await readFile(src))
        .digest("hex") ===
      createHash("sha256")
        .update(await readFile(dest))
        .digest("hex");
  } catch {}
  if (!same) await copyFile(src, dest);
}
await writeFile(
  path.join(root, "evidence", "database-runtime.json"),
  JSON.stringify(
    {
      source: url,
      version: "1.4.0",
      sha256: sha,
      bundleSha256:
        "3a50225198ac67f26f1777dd02cd6a5357ceb0a7ed33fa78db05a2e7a33af295",
      deployment: "local Windows server; seekdb-js TCP; no remote database",
    },
    null,
    2,
  ) + "\n",
);
console.log(
  "Verified local server runtime. Run npm run db:start in a separate terminal.",
);
