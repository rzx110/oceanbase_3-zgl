"""Engineering materials stored and queried in real seekdb over MySQL/TLS."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import ssl
from typing import Any

import certifi
import pymysql


class SeekDB:
    def __init__(self, app_root: Path, state_dir: Path):
        self.root = app_root.resolve()
        self.state_dir = state_dir.resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        config_path = Path(os.environ.get("SDD_SEEKDB_CONFIG", self.root / ".local/d0.json"))
        if not config_path.is_file():
            raise RuntimeError("seekdb is not configured; run python scripts/create_seekdb.py first.")
        config = json.loads(config_path.read_text(encoding="utf-8"))
        self.connection = pymysql.connect(
            host=config["host"], port=int(config["port"]), user=config["username"],
            password=config["password"], database=config["database"], charset="utf8mb4",
            connect_timeout=15, read_timeout=40, write_timeout=40, autocommit=True,
            ssl=ssl.create_default_context(cafile=certifi.where()),
            cursorclass=pymysql.cursors.DictCursor,
        )
        self.trace: list[dict[str, Any]] = []

    def execute(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        # Templates are owned by this module; all source/query values are bound.
        with self.connection.cursor() as cursor:
            cursor.execute(sql.replace("?", "%s"), params or None)
            rows = list(cursor.fetchall()) if cursor.description else []
        self.trace.append({"sql": sql, "params": params or [], "rows": rows})
        return rows

    def close(self):
        self.connection.close()

    def __enter__(self):
        try:
            self.execute("SELECT VERSION() AS version")
            self.execute("""CREATE TABLE IF NOT EXISTS sdd_materials (
              project_id VARCHAR(128) NOT NULL, path VARCHAR(512) NOT NULL,
              kind VARCHAR(16) NOT NULL, sha256 CHAR(64) NOT NULL,
              content LONGTEXT NOT NULL, PRIMARY KEY(project_id,path)
            ) DEFAULT CHARSET=utf8mb4""")
        except Exception:
            self.close()
            raise
        return self

    def __exit__(self, *_):
        self.close()

    def import_project(self, project_root: Path, manifest: dict) -> dict:
        root = project_root.resolve()
        project = manifest["project_id"]
        documents: dict[str, dict] = {}
        for group in manifest["materials"]:
            if group["kind"] not in {"spec", "code", "test"}:
                raise ValueError("material kind must be spec, code, or test")
            for path in sorted(root.glob(group["glob"])):
                if not path.is_file():
                    continue
                path = path.resolve()
                if not path.is_relative_to(root):
                    raise ValueError("material path escapes project root")
                raw = path.read_bytes()
                documents[path.relative_to(root).as_posix()] = {
                    "kind": group["kind"], "content": raw.decode("utf-8"),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
        if not documents:
            raise ValueError("no project materials matched the manifest")
        # Read and validate all inputs before starting the replacement transaction.
        self.execute("START TRANSACTION")
        try:
            self.execute("DELETE FROM sdd_materials WHERE project_id=?", [project])
            for path, item in documents.items():
                self.execute(
                    "INSERT INTO sdd_materials(project_id,path,kind,sha256,content) VALUES (?,?,?,?,?)",
                    [project, path, item["kind"], item["sha256"], item["content"]],
                )
            self.execute("COMMIT")
        except Exception:
            self.execute("ROLLBACK")
            raise
        return {"backend": "seekdb", "project_id": project, "imported": len(documents),
                "sources": [{"path": p, "kind": x["kind"], "sha256": x["sha256"]} for p, x in documents.items()]}

    def select_context(self, project: str, query: str, per_kind: int = 2) -> list[dict]:
        if not 1 <= per_kind <= 10:
            raise ValueError("per_kind must be between 1 and 10")
        terms = list(dict.fromkeys(re.findall(r"[a-z_][a-z_0-9]*|[\u4e00-\u9fff]+", query.lower())))[:24]
        if not terms:
            return []
        # Terms are bound values, never SQL text. Ranking happens inside seekdb.
        score = "+".join("CASE WHEN LOCATE(?,LOWER(content))>0 THEN 1 ELSE 0 END" for _ in terms)
        sql = (f"SELECT path,kind,sha256,content,({score}) AS relevance FROM sdd_materials "
               "WHERE project_id=? AND kind=? HAVING relevance>0 ORDER BY relevance DESC,path ASC LIMIT ?")
        rows = []
        for kind in ("spec", "code", "test"):
            rows.extend(self.execute(sql, [*terms, project, kind, per_kind]))
        return rows
