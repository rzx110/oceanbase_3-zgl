import http from "node:http";
import path from "node:path";
import { readFile, stat } from "node:fs/promises";
import { performance } from "node:perf_hooks";
import { root } from "./scripts/database.mjs";
import { KnowledgeDatabase } from "./src/database.mjs";
const db = await KnowledgeDatabase.open();
const port = Number(process.env.PORT || 8791);
const types = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css",
  ".json": "application/json",
  ".wasm": "application/wasm",
  ".onnx": "application/octet-stream",
  ".txt": "text/plain",
};
const headers = {
  "Cross-Origin-Opener-Policy": "same-origin",
  "Cross-Origin-Embedder-Policy": "require-corp",
  "Cross-Origin-Resource-Policy": "same-origin",
  "X-Content-Type-Options": "nosniff",
  "Cache-Control": "no-cache",
};
const server = http.createServer(async (req, res) => {
  const send = (code, value) => {
    res.writeHead(code, {
      ...headers,
      "Content-Type": "application/json; charset=utf-8",
    });
    res.end(JSON.stringify(value));
  };
  try {
    const url = new URL(req.url, "http://localhost"),
      p = url.pathname;
    if (p.startsWith("/api/")) {
      if (req.method === "GET" && p === "/api/status")
        return send(200, {
          database: db.version,
          deployment:
            process.platform === "win32"
              ? "local Windows seekdb server"
              : "local embedded seekdb",
          serverEmbedding: false,
        });
      if (req.method === "GET" && p === "/api/collections")
        return send(200, await db.list());
      if (req.method === "GET" && p === "/api/model-files")
        return send(
          200,
          JSON.parse(
            await readFile(
              path.join(root, ".local", "model-receipts.json"),
              "utf8",
            ),
          ),
        );
      if (req.method !== "POST") return send(404, { error: "接口不存在" });
      // Local-only API. No cross-origin writes and no permissive CORS.
      if (
        req.headers.origin &&
        !["http://localhost:" + port, "http://127.0.0.1:" + port].includes(
          req.headers.origin,
        )
      )
        return send(403, { error: "仅允许本地页面调用" });
      let body = "",
        size = 0;
      for await (const chunk of req) {
        size += chunk.length;
        if (size > 16 * 1024 * 1024)
          return send(413, { error: "导入内容超过 16 MB" });
        body += chunk;
      }
      const data = JSON.parse(body),
        started = performance.now();
      let result;
      if (p === "/api/collections") result = await db.create(data.label);
      else if (/^\/api\/collections\/edge_[a-f0-9]{32}\/import$/.test(p))
        result = await db.upsert(p.split("/")[3], data);
      else if (/^\/api\/collections\/edge_[a-f0-9]{32}\/search$/.test(p))
        result = await db.search(p.split("/")[3], data);
      else return send(404, { error: "接口不存在" });
      return send(200, { ...result, serverMs: performance.now() - started });
    }
    let base = path.join(root, "dist"),
      relative = decodeURIComponent(p).replace(/^\/+/, "");
    if (p.startsWith("/models-cold/")) {
      base = path.join(root, ".local", "models");
      relative = decodeURIComponent(p.split("/").slice(3).join("/"));
    } else if (p.startsWith("/models/")) {
      base = path.join(root, ".local", "models");
      relative = decodeURIComponent(p.slice(8));
    } else if (p.startsWith("/ort/")) {
      base = path.join(root, "node_modules", "onnxruntime-web", "dist");
      relative = decodeURIComponent(p.slice(5));
    }
    if (!relative) relative = "index.html";
    const file = path.resolve(base, relative);
    if (!file.startsWith(path.resolve(base) + path.sep))
      return send(403, { error: "路径无效" });
    const data = await readFile(file);
    res.writeHead(200, {
      ...headers,
      "Content-Type": types[path.extname(file)] || "application/octet-stream",
      "Content-Length": data.length,
      "Cache-Control": p.startsWith("/models-cold/")
        ? "no-store"
        : p.startsWith("/models/") || p.startsWith("/ort/")
          ? "public, max-age=31536000, immutable"
          : "no-cache",
    });
    res.end(data);
  } catch (e) {
    send(400, { error: e.message });
  }
});
server.listen(port, "127.0.0.1", () =>
  console.log(
    `Edge Notes http://localhost:${port} — ${db.version}; embeddings only in browser`,
  ),
);
let closing = false;
async function close() {
  if (closing) return;
  closing = true;
  server.close();
  await db.close();
  process.exit(0);
}
process.on("SIGINT", close);
process.on("SIGTERM", close);
