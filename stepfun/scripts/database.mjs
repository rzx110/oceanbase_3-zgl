import { randomBytes } from "node:crypto";
import { mkdir, readFile, writeFile, access } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { SeekdbClient } from "seekdb";
export const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);
export const configPath = path.join(root, ".local", "db-config.json");
export async function config() {
  if (process.env.SEEKDB_HOST)
    return {
      host: process.env.SEEKDB_HOST,
      port: Number(process.env.SEEKDB_PORT || 2885),
      database: process.env.SEEKDB_DATABASE || "test",
      user: process.env.SEEKDB_USER || "root",
      password: process.env.SEEKDB_PASSWORD || "",
    };
  try {
    return JSON.parse(await readFile(configPath, "utf8"));
  } catch (e) {
    if (e.code !== "ENOENT") throw e;
  }
  if (process.platform !== "win32") {
    process.env.SEEKDB_BINDINGS_CACHE_DIR ??= path.join(
      root,
      ".local",
      "bindings",
    );
    return { path: path.join(root, ".local", "embedded-db"), database: "test" };
  }
  throw new Error("请先运行 npm run db:setup 和 npm run db:start");
}
export async function initializeWindowsAccount() {
  try {
    await access(configPath);
    return;
  } catch {}
  const password = randomBytes(24).toString("hex");
  const cfg = {
    host: "127.0.0.1",
    port: 2885,
    database: "test",
    user: "root",
    password,
  };
  const c = new SeekdbClient({ ...cfg, password: "" });
  try {
    await c.execute("SELECT 1");
    // Persist a recovery copy before changing the fresh, application-owned instance.
    await writeFile(configPath, JSON.stringify(cfg, null, 2) + "\n", {
      mode: 0o600,
    });
    await c.execute(`ALTER USER 'root'@'%' IDENTIFIED BY '${password}'`);
  } finally {
    await c.close();
  }
  const verify = new SeekdbClient(cfg);
  try {
    await verify.execute("SELECT 1");
  } finally {
    await verify.close();
  }
  console.log(
    "Local account initialized; credentials remain in ignored .local/db-config.json.",
  );
}
await mkdir(path.join(root, ".local"), { recursive: true });
