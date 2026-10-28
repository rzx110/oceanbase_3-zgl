import { spawn } from "node:child_process";
import { mkdir, open, readFile, access } from "node:fs/promises";
import path from "node:path";
import { SeekdbClient } from "seekdb";
import { root, configPath, initializeWindowsAccount } from "./database.mjs";
if (process.platform !== "win32") {
  console.log("Embedded database starts with npm start on Linux/macOS.");
  process.exit(0);
}
const dir = path.join(root, ".local", "seekdb-server");
await mkdir(dir, { recursive: true });
let saved;
try {
  saved = JSON.parse(await readFile(configPath, "utf8"));
} catch {}
const probe = new SeekdbClient(
  saved || {
    host: "127.0.0.1",
    port: 2885,
    database: "test",
    user: "root",
    password: "",
  },
);
try {
  await probe.execute("SELECT 1");
  await probe.close();
  await initializeWindowsAccount();
  console.log("Application database is already running.");
  process.exit(0);
} catch {
  await probe.close();
}
const executable = path.join(root, ".local", "windows-runtime", "seekdb.exe");
await access(executable);
const log = await open(path.join(root, ".local", "database-console.log"), "a");
const child = spawn(
  executable,
  [
    "--nodaemon",
    "--base-dir",
    dir,
    "--port",
    "2885",
    "--log-level",
    "WARN",
    "--parameter",
    "mysql_port_mode=specified",
    "--parameter",
    "memory_budget=1G",
    "--parameter",
    "log_disk_size=2G",
  ],
  { windowsHide: true, stdio: ["ignore", log.fd, log.fd] },
);
child.on("error", (e) => console.error(e.message));
let ready = false;
for (let i = 0; i < 120; i++) {
  if (child.exitCode !== null)
    throw new Error(
      `seekdb exited ${child.exitCode}; inspect .local/seekdb-server/log/seekdb.log`,
    );
  const c = new SeekdbClient(
    saved || {
      host: "127.0.0.1",
      port: 2885,
      database: "test",
      user: "root",
      password: "",
    },
  );
  try {
    await c.execute("SELECT 1");
    ready = true;
    break;
  } catch {
  } finally {
    await c.close();
  }
  await new Promise((r) => setTimeout(r, 1000));
}
if (!ready) {
  child.kill();
  throw new Error("seekdb startup exceeded 120 seconds; inspect native logs");
}
await initializeWindowsAccount();
console.log(
  "Local seekdb ready on 127.0.0.1:2885. Keep this process open. Ctrl+C stops only this child.",
);
process.on("SIGINT", () => child.kill());
process.on("SIGTERM", () => child.kill());
child.on("exit", (code) => {
  console.log(`seekdb stopped (${code})`);
  process.exitCode = code || 0;
});
